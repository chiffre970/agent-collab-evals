import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { OpenRouter } from "@openrouter/sdk";

const REQUIRED_KEY = "OPENROUTER_API_KEY";
const DEFAULT_MODEL = "deepseek/deepseek-v4-flash-0731";
const DEFAULT_PROVIDER = "DeepInfra";
const DEFAULT_REASONING_EFFORT = "low";
const DEFAULT_MAX_COMPLETION_TOKENS = 512;
const PREFLIGHT_PROMPT = "How many r's are in the word 'strawberry'? Answer briefly.";

function requiredEnvironment(name) {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} is missing. Add it to the ignored local .env file.`);
  }
  return value;
}

function booleanEnvironment(name, fallback) {
  const value = process.env[name]?.trim().toLowerCase();
  if (!value) return fallback;
  if (value === "true") return true;
  if (value === "false") return false;
  throw new Error(`${name} must be either true or false.`);
}

function positiveIntegerEnvironment(name, fallback) {
  const value = process.env[name]?.trim();
  if (!value) return fallback;
  if (!/^[1-9]\d*$/.test(value)) {
    throw new Error(`${name} must be a positive integer.`);
  }
  const parsed = Number.parseInt(value, 10);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer.`);
  }
  return parsed;
}

function dataCollectionEnvironment() {
  const value =
    process.env.OPENROUTER_DATA_COLLECTION?.trim().toLowerCase() || "deny";
  if (value !== "allow" && value !== "deny") {
    throw new Error(
      "OPENROUTER_DATA_COLLECTION must be either allow or deny.",
    );
  }
  return value;
}

async function main() {
  const apiKey = requiredEnvironment(REQUIRED_KEY);
  const requestedModel = process.env.OPENROUTER_MODEL?.trim() || DEFAULT_MODEL;
  const requestedProvider =
    process.env.OPENROUTER_PROVIDER?.trim() || DEFAULT_PROVIDER;
  const allowFallbacks = booleanEnvironment(
    "OPENROUTER_ALLOW_FALLBACKS",
    false,
  );
  const dataCollection = dataCollectionEnvironment();
  const zdr = booleanEnvironment("OPENROUTER_ZDR", true);
  const reasoningEffort =
    process.env.OPENROUTER_REASONING_EFFORT?.trim() ||
    DEFAULT_REASONING_EFFORT;
  const maxCompletionTokens = positiveIntegerEnvironment(
    "OPENROUTER_MAX_COMPLETION_TOKENS",
    DEFAULT_MAX_COMPLETION_TOKENS,
  );

  const clientOptions = {
    apiKey,
    appTitle:
      process.env.OPENROUTER_APP_TITLE?.trim() || "Agent Collaboration Evals",
  };
  const httpReferer = process.env.OPENROUTER_HTTP_REFERER?.trim();
  if (httpReferer) clientOptions.httpReferer = httpReferer;

  const openrouter = new OpenRouter(clientOptions);
  const startedAt = new Date();
  const stream = await openrouter.chat.send({
    xOpenRouterMetadata: "enabled",
    chatRequest: {
      model: requestedModel,
      messages: [{ role: "user", content: PREFLIGHT_PROMPT }],
      // The pinned DeepSeek endpoint currently advertises `max_tokens`, not
      // `max_completion_tokens`. Keep the endpoint-facing spelling while
      // retaining the provider-parameter conformance check below.
      maxTokens: maxCompletionTokens,
      provider: {
        order: [requestedProvider],
        only: [requestedProvider],
        allowFallbacks,
        dataCollection,
        requireParameters: true,
        zdr,
      },
      reasoningEffort,
      stream: true,
    },
  });

  let response = "";
  let requestId;
  let returnedModel;
  let systemFingerprint;
  let usage;
  let routingMetadata;
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
    routingMetadata = chunk.openrouterMetadata || routingMetadata;

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

  const attempts = routingMetadata?.attempts || [];
  const successfulAttempt = attempts.findLast(
    (attempt) => attempt.status >= 200 && attempt.status < 300,
  );
  const finishedAt = new Date();
  const canaryPassed = /\b(?:3|three)\b/i.test(response);
  const receipt = {
    schemaVersion: 1,
    startedAt: startedAt.toISOString(),
    finishedAt: finishedAt.toISOString(),
    elapsedMs: finishedAt.getTime() - startedAt.getTime(),
    requestId: requestId || null,
    requestedModel,
    returnedModel: returnedModel || null,
    requestedProvider,
    returnedProvider: successfulAttempt?.provider || requestedProvider,
    allowFallbacks,
    dataCollection,
    zdr,
    reasoningEffort,
    maxCompletionTokens,
    systemFingerprint: systemFingerprint || null,
    finishReason: finishReason || null,
    canaryPassed,
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

  console.log(
    JSON.stringify(
      {
        ok: true,
        requestedModel,
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
