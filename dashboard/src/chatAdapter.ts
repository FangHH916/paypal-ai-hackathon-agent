// AG Studio's AI assistant (the Studio Agent Framework) on Star's own model. Star talks to any OpenAI-compatible
// /chat/completions API, so this adapter maps Studio's conversation to Chat Completions and back, and sends it to
// Star's server (POST /api/dashboard/ai), which holds the key, fixes the model, caps the output and rate-limits.
// One request per turn; the finished reply is replayed as Studio's events. Studio runs the tools it asks for in the
// browser: they read and rearrange the dashboard, and none of them can reach a payment.
import type {
  AgAiConversationItem,
  AgAiEvent,
  AgAiOutputItem,
  AgAiToolSchema,
  AgLlmAdapter,
  AgLlmRequest,
  AgLlmResponse,
  AgLlmResponseHandler,
} from 'ag-studio';

type ChatMessage =
  | { role: 'system' | 'user'; content: string }
  | { role: 'assistant'; content: string | null; tool_calls?: ChatToolCall[] }
  | { role: 'tool'; tool_call_id: string; content: string };
type ChatToolCall = { id: string; type: 'function'; function: { name: string; arguments: string } };

const text = (parts: { type: string; text?: string; refusal?: string }[]) =>
  parts.map((p) => p.text ?? p.refusal ?? '').filter(Boolean).join('\n');

function encode(request: AgLlmRequest): ChatMessage[] {
  const out: ChatMessage[] = [];
  let system = request.instructions ?? '';
  if (request.responseFormat?.type === 'json') {
    // Most compatible APIs take a JSON object reply, not a strict schema, so the schema goes in the instructions.
    const f = request.responseFormat as { name?: string; schema?: unknown };
    system += `\n\nReply with one JSON object only, matching this JSON Schema (${f.name ?? 'result'}):\n${JSON.stringify(f.schema)}`;
  }
  if (system.trim()) out.push({ role: 'system', content: system.trim() });
  for (const item of request.input as AgAiConversationItem[]) {
    const it = item as unknown as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    if (it.kind === 'input' && it.type === 'message') {
      out.push({ role: it.role === 'user' ? 'user' : 'system', content: text(it.content) });
    } else if (it.kind === 'output' && it.type === 'message') {
      out.push({ role: 'assistant', content: text(it.content) });
    } else if (it.kind === 'output' && it.type === 'function_call') {
      const call: ChatToolCall = { id: it.callId, type: 'function', function: { name: it.name, arguments: it.arguments || '{}' } };
      const last = out[out.length - 1];
      if (last && last.role === 'assistant' && last.tool_calls) last.tool_calls.push(call);
      else out.push({ role: 'assistant', content: null, tool_calls: [call] });
    } else if (it.type === 'function_call_output') {
      out.push({ role: 'tool', tool_call_id: it.callId, content: typeof it.output === 'string' ? it.output : JSON.stringify(it.output) });
    }
    // Reasoning items are the model's own notes from an earlier turn; Chat Completions has no place for them.
  }
  return out;
}

const tools = (list: AgAiToolSchema[] = []) =>
  list.map((t) => ({ type: 'function' as const, function: { name: t.name, description: t.description, parameters: t.parameters } }));

const toolChoice = (c: AgLlmRequest['toolChoice']) =>
  !c ? undefined : typeof c === 'string' ? c : { type: 'function', function: { name: c.name } };

function decode(data: Record<string, any>, model: string | undefined): { response: AgLlmResponse; events: AgAiEvent[] } { // eslint-disable-line @typescript-eslint/no-explicit-any
  const msg = data.choices?.[0]?.message ?? {};
  const id = data.id || `star-${Date.now()}`;
  const output: AgAiOutputItem[] = [];
  const events: AgAiEvent[] = [];
  if (typeof msg.content === 'string' && msg.content.trim()) {
    const messageId = `${id}-msg`;
    output.push({ id: messageId, kind: 'output', type: 'message', role: 'assistant', status: 'completed',
      content: [{ type: 'text', text: msg.content, annotations: [] }] } as AgAiOutputItem);
    events.push({ type: 'TEXT_MESSAGE_START', messageId, role: 'assistant' } as AgAiEvent,
      { type: 'TEXT_MESSAGE_CONTENT', messageId, delta: msg.content } as AgAiEvent,
      { type: 'TEXT_MESSAGE_END', messageId } as AgAiEvent);
  }
  for (const [i, call] of (msg.tool_calls ?? []).entries()) {
    const callId = call.id || `${id}-call-${i}`;
    const args = call.function?.arguments || '{}';
    output.push({ id: `${id}-fc-${i}`, kind: 'output', type: 'function_call', callId, name: call.function?.name,
      arguments: args, status: 'completed' } as AgAiOutputItem);
    events.push({ type: 'TOOL_CALL_START', toolCallId: callId, toolCallName: call.function?.name } as AgAiEvent,
      { type: 'TOOL_CALL_ARGS', toolCallId: callId, delta: args } as AgAiEvent,
      { type: 'TOOL_CALL_END', toolCallId: callId } as AgAiEvent);
  }
  const response = { id, createdAt: Date.now(), output, status: 'completed', model: data.model || model } as AgLlmResponse;
  return { response, events };
}

function run(sessionId: string, request: AgLlmRequest, signal?: AbortSignal): AgLlmResponseHandler {
  let done: (r: AgLlmResponse) => void = () => {};
  let fail: (e: Error) => void = () => {};
  const complete = new Promise<AgLlmResponse>((resolve, reject) => {
    done = resolve;
    fail = reject;
  });
  void complete.catch(() => {});

  async function* stream(): AsyncIterableIterator<AgAiEvent> {
    try {
      const r = await fetch('/api/dashboard/ai', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          messages: encode(request),
          tools: tools(request.tools),
          tool_choice: request.tools?.length ? toolChoice(request.toolChoice) : undefined,
          json: request.responseFormat?.type === 'json',
        }),
        signal,
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error?.message || data.error || `The assistant isn't available (${r.status}).`);
      const { response, events } = decode(data, request.model?.id);
      yield* events;
      done(response);
    } catch (e) {
      fail(e instanceof Error ? e : new Error(String(e)));
    }
  }
  const it = stream();
  return { stream: { [Symbol.asyncIterator]: () => it }, complete };
}

export function starChatAdapter(sessionId: string): AgLlmAdapter {
  return { executeTurn: (request, options) => run(sessionId, request, options?.signal) };
}
