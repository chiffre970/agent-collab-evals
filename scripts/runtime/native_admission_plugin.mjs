// Enforcement integration, not observational instrumentation. This module
// authorizes the stock task tool without changing its arguments or outputs.
export default async function nativeAdmission(_context, options) {
  if (!options?.endpoint || !options?.token) throw new Error("native admission authority is required");
  const permits = new Map();
  async function call(operation, args) {
    const response = await fetch(options.endpoint, {
      method: "POST",
      headers: { Authorization: `Bearer ${options.token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ operation, arguments: args }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) throw new Error("Native fleet admission denied or unavailable");
    return (await response.json()).result;
  }
  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool !== "task") return;
      const result = await call("reserve", {
        session_id: input.sessionID, call_id: input.callID,
        task_id: output.args.task_id ?? null,
        subagent_type: output.args.subagent_type,
      });
      if (typeof result?.permit !== "string") throw new Error("Native permit missing");
      permits.set(input.callID, result.permit);
    },
    "tool.execute.after": async (input, output) => {
      if (input.tool !== "task") return;
      const permit = permits.get(input.callID);
      if (!permit || typeof output.metadata?.sessionId !== "string" || output.metadata?.background === true) {
        throw new Error("Native task has no terminal admission evidence");
      }
      await call("complete", { permit, child_session_id: output.metadata.sessionId });
      permits.delete(input.callID);
    },
  };
}
