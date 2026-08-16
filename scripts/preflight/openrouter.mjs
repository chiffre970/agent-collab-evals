import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

import { OpenRouter } from "@openrouter/sdk";

const REQUIRED_KEY = "OPENROUTER_API_KEY";
const DEFAULT_PROFILE_PATH =
  "config/model_profiles/deepseek-v4-flash-openrouter-deepinfra-development.json";
const PROFILE_PATH = /^config\/model_profiles\/[a-z0-9][a-z0-9._-]*\.json$/;
const PREFLIGHT_PROMPT = "How many r's are in the word 'strawberry'? Answer briefly.";

function parseArguments(args) {
  let profilePath = DEFAULT_PROFILE_PATH;
  let validateOnly = false;
  let profileSeen = false;

  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === "--validate-profile") {
      if (validateOnly) throw new Error("--validate-profile may be supplied once.");
      validateOnly = true;
      continue;
    }
    if (argument === "--profile") {
      if (profileSeen) throw new Error("--profile may be supplied once.");
      const value = args[index + 1];
      if (!value || value.startsWith("--")) {
        throw new Error("--profile requires a committed model-profile path.");
      }
      profilePath = value;
      profileSeen = true;
      index += 1;
      continue;
    }
    throw new Error(`Unknown argument: ${argument}`);
  }

  if (!PROFILE_PATH.test(profilePath)) {
    throw new Error(
      "--profile must name a JSON file directly under config/model_profiles/.",
    );
  }
  return { profilePath, validateOnly };
}

function requiredEnvironment(name) {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} is missing. Add it to the ignored local .env file.`);
  }
  return value;
}

function assertPlainObject(value, path) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${path} must be an object.`);
  }
}

function assertExactKeys(value, path, keys) {
  assertPlainObject(value, path);
  const expected = new Set(keys);
  const missing = keys.filter((key) => !Object.hasOwn(value, key));
  const unknown = Object.keys(value).filter((key) => !expected.has(key));
  if (missing.length || unknown.length) {
    const details = [];
    if (missing.length) details.push(`missing: ${missing.join(", ")}`);
    if (unknown.length) details.push(`unknown: ${unknown.join(", ")}`);
    throw new Error(`${path} has invalid keys (${details.join("; ")}).`);
  }
}

function assertNonEmptyString(value, path) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${path} must be a non-empty string.`);
  }
}

function assertBoolean(value, path) {
  if (typeof value !== "boolean") {
    throw new Error(`${path} must be a boolean.`);
  }
}

function assertStringArray(value, path) {
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error(`${path} must be a non-empty array.`);
  }
  for (const [index, item] of value.entries()) {
    assertNonEmptyString(item, `${path}[${index}]`);
  }
  if (new Set(value).size !== value.length) {
    throw new Error(`${path} must not contain duplicates.`);
  }
}

function validateProfile(profile) {
  assertExactKeys(profile, "model profile", [
    "schema_version",
    "profile_id",
    "status",
    "transport",
    "endpoint",
    "requested_model",
    "expected_stream_model",
    "expected_metadata_model",
    "provider",
    "inference",
    "preflight",
    "client",
  ]);
  for (const key of [
    "schema_version",
    "profile_id",
    "status",
    "transport",
    "endpoint",
    "requested_model",
    "expected_stream_model",
    "expected_metadata_model",
  ]) {
    assertNonEmptyString(profile[key], `model profile.${key}`);
  }
  if (profile.schema_version !== "model-profile/v0alpha1") {
    throw new Error("model profile.schema_version is not supported.");
  }
  if (profile.status !== "development") {
    throw new Error("The preflight only accepts a development model profile.");
  }
  if (profile.transport !== "openrouter") {
    throw new Error("The preflight only accepts the OpenRouter transport.");
  }
  const endpoint = new URL(profile.endpoint);
  if (endpoint.protocol !== "https:" || endpoint.username || endpoint.password) {
    throw new Error("model profile.endpoint must be an HTTPS URL without credentials.");
  }

  assertExactKeys(profile.provider, "model profile.provider", [
    "order",
    "only",
    "expected",
    "allow_fallbacks",
    "data_collection",
    "require_parameters",
    "zdr",
  ]);
  assertStringArray(profile.provider.order, "model profile.provider.order");
  assertStringArray(profile.provider.only, "model profile.provider.only");
  assertNonEmptyString(profile.provider.expected, "model profile.provider.expected");
  for (const provider of profile.provider.order) {
    if (!profile.provider.only.includes(provider)) {
      throw new Error("Every ordered provider must also appear in provider.only.");
    }
  }
  if (!profile.provider.only.includes(profile.provider.expected)) {
    throw new Error("provider.expected must appear in provider.only.");
  }
  for (const key of ["allow_fallbacks", "require_parameters", "zdr"]) {
    assertBoolean(profile.provider[key], `model profile.provider.${key}`);
  }
  if (!["allow", "deny"].includes(profile.provider.data_collection)) {
    throw new Error("model profile.provider.data_collection must be allow or deny.");
  }

  assertExactKeys(profile.inference, "model profile.inference", [
    "reasoning_effort",
  ]);
  assertNonEmptyString(
    profile.inference.reasoning_effort,
    "model profile.inference.reasoning_effort",
  );
  assertExactKeys(profile.preflight, "model profile.preflight", [
    "max_completion_tokens",
  ]);
  if (
    !Number.isSafeInteger(profile.preflight.max_completion_tokens) ||
    profile.preflight.max_completion_tokens <= 0
  ) {
    throw new Error(
      "model profile.preflight.max_completion_tokens must be a positive integer.",
    );
  }
  assertExactKeys(profile.client, "model profile.client", ["app_title"]);
  assertNonEmptyString(profile.client.app_title, "model profile.client.app_title");
}

async function loadProfile(profilePath) {
  const profileUrl = new URL(`../../${profilePath}`, import.meta.url);
  const bytes = await readFile(profileUrl);
  let profile;
  try {
    profile = JSON.parse(bytes.toString("utf8"));
  } catch (error) {
    throw new Error(`Unable to parse ${profilePath}: ${error.message}`);
  }
  validateProfile(profile);
  return {
    profile,
    digest: `sha256:${createHash("sha256").update(bytes).digest("hex")}`,
  };
}

async function getGenerationMetadata(openrouter, requestId) {
  let lastError;
  for (const waitMs of [0, 500, 1000, 2000, 4000, 8000]) {
    if (waitMs) await delay(waitMs);
    try {
      return await openrouter.generations.getGeneration({ id: requestId });
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
}

async function main() {
  const { profilePath, validateOnly } = parseArguments(process.argv.slice(2));
  const { profile, digest: profileDigest } = await loadProfile(profilePath);
  if (validateOnly) {
    console.log(
      JSON.stringify(
        {
          ok: true,
          profileId: profile.profile_id,
          profilePath,
          profileDigest,
          model: profile.requested_model,
          provider: profile.provider.expected,
        },
        null,
        2,
      ),
    );
    return;
  }

  const apiKey = requiredEnvironment(REQUIRED_KEY);
  const openrouter = new OpenRouter({
    apiKey,
    appTitle: profile.client.app_title,
    serverURL: profile.endpoint,
  });
  const startedAt = new Date();
  const stream = await openrouter.chat.send({
    xOpenRouterMetadata: "enabled",
    chatRequest: {
      model: profile.requested_model,
      messages: [{ role: "user", content: PREFLIGHT_PROMPT }],
      // The pinned DeepSeek endpoint currently advertises `max_tokens`, not
      // `max_completion_tokens`. Keep the endpoint-facing spelling while
      // retaining the provider-parameter conformance check below.
      maxTokens: profile.preflight.max_completion_tokens,
      provider: {
        order: profile.provider.order,
        only: profile.provider.only,
        allowFallbacks: profile.provider.allow_fallbacks,
        dataCollection: profile.provider.data_collection,
        requireParameters: profile.provider.require_parameters,
        zdr: profile.provider.zdr,
      },
      reasoningEffort: profile.inference.reasoning_effort,
      stream: true,
    },
  });

  let response = "";
  let requestId;
  let returnedModel;
  let systemFingerprint;
  let usage;
  let finishReason;

  for await (const chunk of stream) {
    if (chunk.error) {
      throw new Error(
        `OpenRouter stream error ${chunk.error.code}: ${chunk.error.message}`,
      );
    }

    requestId = chunk.id || requestId;
    returnedModel = chunk.model || returnedModel;
    systemFingerprint = chunk.systemFingerprint || systemFingerprint;
    const choice = chunk.choices[0];
    finishReason = choice?.finishReason || finishReason;
    const content = choice?.delta?.content;
    if (content) {
      response += content;
      process.stdout.write(content);
    }

    if (chunk.usage) usage = chunk.usage;
  }

  process.stdout.write("\n");
  if (!usage) {
    throw new Error("The stream completed without a usage receipt.");
  }

  let generationMetadata = null;
  let generationMetadataError = null;
  if (requestId) {
    try {
      generationMetadata = await getGenerationMetadata(openrouter, requestId);
    } catch (error) {
      generationMetadataError = error.message;
    }
  } else {
    generationMetadataError = "The stream did not return a request ID.";
  }

  const finishedAt = new Date();
  const canaryPassed = /\b(?:3|three)\b/i.test(response);
  const returnedProvider = generationMetadata?.data?.providerName ?? null;
  const metadataModel = generationMetadata?.data?.model ?? null;
  const conformanceFailures = [];
  if ((returnedModel || null) !== profile.expected_stream_model) {
    conformanceFailures.push(
      `stream returned model ${returnedModel || "<missing>"}; expected ${profile.expected_stream_model}`,
    );
  }
  if (metadataModel !== profile.expected_metadata_model) {
    conformanceFailures.push(
      `generation metadata returned model ${metadataModel || "<missing>"}; expected ${profile.expected_metadata_model}`,
    );
  }
  if (returnedProvider !== profile.provider.expected) {
    conformanceFailures.push(
      `generation metadata returned provider ${returnedProvider || "<missing>"}; expected ${profile.provider.expected}`,
    );
  }
  if (generationMetadataError) {
    conformanceFailures.push(
      `generation metadata could not be obtained: ${generationMetadataError}`,
    );
  }

  const receipt = {
    schemaVersion: 2,
    profile: {
      id: profile.profile_id,
      path: profilePath,
      digest: profileDigest,
      status: profile.status,
    },
    startedAt: startedAt.toISOString(),
    finishedAt: finishedAt.toISOString(),
    elapsedMs: finishedAt.getTime() - startedAt.getTime(),
    requestId: requestId || null,
    requestedModel: profile.requested_model,
    returnedModel: returnedModel || null,
    metadataModel,
    requestedProviders: profile.provider.only,
    returnedProvider,
    allowFallbacks: profile.provider.allow_fallbacks,
    dataCollection: profile.provider.data_collection,
    zdr: profile.provider.zdr,
    reasoningEffort: profile.inference.reasoning_effort,
    maxCompletionTokens: profile.preflight.max_completion_tokens,
    systemFingerprint: systemFingerprint || null,
    finishReason: finishReason || null,
    canaryPassed,
    conformanceFailures,
    generationMetadata: generationMetadata?.data
      ? {
          id: generationMetadata.data.id,
          providerName: generationMetadata.data.providerName,
          model: generationMetadata.data.model,
          dataRegion: generationMetadata.data.dataRegion,
          serviceTier: generationMetadata.data.serviceTier,
          streamed: generationMetadata.data.streamed,
          latencyMs: generationMetadata.data.latency,
          generationTimeMs: generationMetadata.data.generationTime,
          totalCostUsd: generationMetadata.data.totalCost,
        }
      : null,
    usage: {
      promptTokens: usage.promptTokens,
      completionTokens: usage.completionTokens,
      reasoningTokens:
        usage.completionTokensDetails?.reasoningTokens ?? null,
      cachedPromptTokens: usage.promptTokensDetails?.cachedTokens ?? null,
      totalTokens: usage.totalTokens,
      costUsd: usage.cost ?? null,
    },
    response,
  };

  const receiptDirectory = join("tmp", "preflight");
  await mkdir(receiptDirectory, { recursive: true });
  const timestamp = finishedAt.toISOString().replaceAll(":", "-");
  const receiptPath = join(
    receiptDirectory,
    `openrouter-${timestamp}.json`,
  );
  await writeFile(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`, {
    mode: 0o600,
  });

  if (!response.trim()) {
    throw new Error(
      `The model returned no visible assistant content. Receipt: ${receiptPath}`,
    );
  }
  if (!canaryPassed) {
    throw new Error(
      `The response failed the strawberry canary. Receipt: ${receiptPath}`,
    );
  }
  if (conformanceFailures.length) {
    throw new Error(
      `The response failed model-profile conformance: ${conformanceFailures.join("; ")}. Receipt: ${receiptPath}`,
    );
  }

  console.log(
    JSON.stringify(
      {
        ok: true,
        profileId: profile.profile_id,
        profileDigest,
        requestedModel: profile.requested_model,
        returnedModel: receipt.returnedModel,
        provider: receipt.returnedProvider,
        reasoningTokens: receipt.usage.reasoningTokens,
        totalTokens: receipt.usage.totalTokens,
        costUsd: receipt.usage.costUsd,
        receiptPath,
      },
      null,
      2,
    ),
  );
}

main().catch((error) => {
  console.error(`OpenRouter preflight failed: ${error.message}`);
  process.exitCode = 1;
});
