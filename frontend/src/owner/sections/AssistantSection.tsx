import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { errorMessage } from "../../shared/api";
import { Button } from "../../shared/components/Button";
import { Badge, Card } from "../../shared/components/Display";
import { Checkbox, TextArea } from "../../shared/components/Field";
import type { AssistantReply } from "../../shared/types";
import { api, casePath } from "../api";
import { useCase } from "../CaseContext";

interface Message {
  role: "user" | "assistant";
  text: string;
  meta?: string;
  error?: boolean;
}

const SUGGESTIONS = [
  "What should I do next?",
  "Which gates are still blocking release?",
  "Where else could assets be hiding?",
  "How do I claim a mutual fund after death?",
  "Are there any fraud warning signs?",
];

export function AssistantSection() {
  const { caseId, caseItem, changed } = useCase();
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      text: `Ask me anything about ${caseItem.subject_name}'s case. I give organisational guidance, not legal or financial advice.`,
    },
  ]);
  const [question, setQuestion] = useState("");
  const [useLlm, setUseLlm] = useState(false);
  const [busy, setBusy] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [messages, busy]);

  async function ask(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    const llm = useLlm;
    setMessages((m) => [...m, { role: "user", text: q }]);
    setQuestion("");
    setUseLlm(false); // consent is per question
    setBusy(true);
    try {
      const reply = await api.post<AssistantReply>(casePath(caseId, "/assistant"), {
        question: q,
        use_llm: llm,
        consent_to_send_case_summary_to_mistral: llm,
      });
      const t = reply.trace ?? {};
      const meta = [
        reply.llm_used ? "Answered with the LLM" : "Answered locally (no data left this server)",
        t.skills_loaded?.length ? `skills: ${t.skills_loaded.join(", ")}` : "",
        t.tools_called?.length ? `tools: ${t.tools_called.join(", ")}` : "",
        t.resources_read?.length ? `references: ${t.resources_read.join(", ")}` : "",
      ]
        .filter(Boolean)
        .join(" · ");
      setMessages((m) => [...m, { role: "assistant", text: reply.answer, meta: [meta, reply.note].filter(Boolean).join("\n") }]);
      changed();
    } catch (error) {
      setMessages((m) => [...m, { role: "assistant", text: `I couldn't answer that: ${errorMessage(error)}`, error: true }]);
    } finally {
      setBusy(false);
      formRef.current?.querySelector("textarea")?.focus();
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(question);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask(question);
    }
  }

  return (
    <Card
      title="Case assistant"
      actions={<Badge tone={useLlm ? "warning" : "success"}>{useLlm ? "LLM for next question" : "Local mode"}</Badge>}
      description="Loads only the skills a question needs. Without the LLM box ticked, nothing leaves this server."
    >
      <div className="chat">
        <div ref={logRef} className="chat-log" role="log" aria-live="polite" aria-relevant="additions" aria-label="Conversation" tabIndex={0}>
          {messages.map((m, i) => (
            <div key={i} className={`msg msg-${m.role}`}>
              <span className="msg-who">{m.role === "user" ? "You" : "Trove assistant"}</span>
              {m.text}
              {m.meta && <div className="msg-trace">{m.meta}</div>}
            </div>
          ))}
          {busy && (
            <div className="msg msg-assistant" aria-label="Trove assistant is thinking">
              <span className="msg-who">Trove assistant</span>
              <span className="spinner" aria-hidden="true" /> Thinking…
            </div>
          )}
        </div>

        <form ref={formRef} className="stack" onSubmit={submit}>
          <div className="suggestions" role="group" aria-label="Suggested questions">
            {SUGGESTIONS.map((s) => (
              <Button key={s} size="sm" onClick={() => void ask(s)} disabled={busy}>
                {s}
              </Button>
            ))}
          </div>
          <TextArea
            label="Your question"
            hint="Press Enter to send, Shift+Enter for a new line."
            name="question"
            rows={3}
            maxLength={5000}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKeyDown}
            required
          />
          <Checkbox
            label="Use the Mistral LLM for this question"
            hint="I consent to send my question and minimised case facts (counts, statuses, institution names — no names, contacts, IDs or account numbers). Applies to one question only."
            checked={useLlm}
            onChange={(e) => setUseLlm(e.target.checked)}
          />
          <div className="button-row">
            <Button type="submit" variant="primary" busy={busy} busyLabel="Thinking…" disabled={!question.trim()}>
              Ask Trove
            </Button>
          </div>
        </form>
      </div>
    </Card>
  );
}
