import { useEffect, useRef, useState } from 'react';
import { Sparkles, Send, RotateCcw, Database } from 'lucide-react';
import { PAGE_BG } from '../components/boardTheme.jsx';

// "Ask Genie" — natural-language questions over the airport's Delta tables, answered by
// a Databricks AI/BI Genie space. The browser only talks to this app (/api/genie/*); the
// app calls the Genie Conversation API as its service principal (server/genie.py).
//
// Flow per question: POST /api/genie/ask → {conversation_id, message_id}; then poll
// GET /api/genie/message until COMPLETED / FAILED. Follow-ups reuse the conversation, so
// Genie keeps context ("now only for LHR").

const POLL_MS = 1500;
const MAX_WAIT_MS = 120000;
const STARTERS = [
  'Which airlines had the highest average departure delay at DOH today?',
  'How many arrivals and departures per hour at LHR in the last 12 hours?',
  'Which DOH flights are delayed by more than 60 minutes?',
  'Compare on-time performance (delay under 15 minutes) for DOH vs LHR.',
];
const STATUS_TEXT = {
  SUBMITTED: 'Sending to Genie…', FILTERING_CONTEXT: 'Reading the space…', ASKING_AI: 'Thinking…',
  PENDING_WAREHOUSE: 'Starting the SQL warehouse…', EXECUTING_QUERY: 'Running SQL…', FETCHING_METADATA: 'Reading table metadata…',
};

export default function AskGeniePage() {
  const [space, setSpace] = useState(null);
  const [turns, setTurns] = useState([]);           // [{q, status, answer, error}]
  const [conversationId, setConversationId] = useState(null);
  const [input, setInput] = useState('');
  const busy = turns.some((t) => !t.done);
  const endRef = useRef(null);

  useEffect(() => {
    fetch('/api/genie/space').then((r) => r.json()).then(setSpace).catch(() => setSpace({ error: 'unreachable' }));
  }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [turns]);

  const update = (i, patch) => setTurns((ts) => ts.map((t, k) => (k === i ? { ...t, ...patch } : t)));

  async function ask(question) {
    const q = question.trim();
    if (!q || busy) return;
    setInput('');
    const i = turns.length;
    setTurns((ts) => [...ts, { q, status: 'SUBMITTED', done: false }]);
    try {
      const r = await fetch('/api/genie/ask', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q, conversation_id: conversationId }),
      });
      const ids = await r.json();
      if (!r.ok || ids.error) throw new Error(ids.error || `HTTP ${r.status}`);
      setConversationId(ids.conversation_id);
      const started = Date.now();
      for (;;) {
        await new Promise((res) => setTimeout(res, POLL_MS));
        const m = await (await fetch(`/api/genie/message?conversation_id=${encodeURIComponent(ids.conversation_id)}&message_id=${encodeURIComponent(ids.message_id)}`)).json();
        if (m.status === 'COMPLETED') { update(i, { status: m.status, answer: m, done: true }); return; }
        if (['FAILED', 'CANCELLED', 'QUERY_RESULT_EXPIRED'].includes(m.status) || m.error) {
          update(i, { status: m.status || 'FAILED', answer: m, error: m.error || `Genie returned ${m.status}`, done: true });
          return;
        }
        update(i, { status: m.status });
        if (Date.now() - started > MAX_WAIT_MS) throw new Error('Genie did not answer within 2 minutes');
      }
    } catch (e) {
      update(i, { status: 'FAILED', error: String(e.message || e), done: true });
    }
  }

  function reset() { setTurns([]); setConversationId(null); }

  return (
    <div style={{ height: '100%', width: '100%', display: 'flex', flexDirection: 'column', background: PAGE_BG }}>
      <div className="panel" style={{
        height: 56, flexShrink: 0, display: 'flex', alignItems: 'center', padding: '0 16px', gap: 10,
        borderRadius: 0, borderLeft: 'none', borderRight: 'none', borderTop: 'none',
      }}>
        <Sparkles size={20} className="accent" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 700, lineHeight: 1.1 }}>Ask Genie</div>
          <div className="muted" style={{ fontSize: 11, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {space?.title ? <>Genie space · <span style={{ color: 'var(--text-primary)' }}>{space.title}</span></> :
              space?.error ? `Genie unavailable: ${space.error}` : 'Connecting to Genie…'}
          </div>
        </div>
        {turns.length > 0 && (
          <button onClick={reset} disabled={busy} className="tab-link" style={BTN}>
            <RotateCcw size={13} /> New conversation
          </button>
        )}
      </div>

      <div style={{ flex: 1, overflow: 'auto' }}>
        <div style={{ maxWidth: 980, margin: '0 auto', padding: 16 }}>
          {turns.length === 0 && (
            <div style={{ marginTop: 24 }}>
              <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 6 }}>Ask about flights at DOH and LHR</div>
              <div className="muted" style={{ fontSize: 12, marginBottom: 16, lineHeight: 1.6 }}>
                Genie turns your question into SQL over the airport's Delta tables in Unity Catalog and runs it
                on a SQL warehouse. Follow-up questions keep the context of the conversation.
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 10 }}>
                {STARTERS.map((s) => (
                  <button key={s} onClick={() => ask(s)} className="card" style={{ ...CHIP, textAlign: 'left' }}>{s}</button>
                ))}
              </div>
            </div>
          )}

          {turns.map((t, i) => (
            <div key={i} style={{ marginBottom: 18 }}>
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
                <div style={{ maxWidth: '80%', padding: '9px 13px', borderRadius: 12, fontSize: 13,
                              background: 'var(--accent-blue)', color: '#0a0e1a', fontWeight: 500 }}>{t.q}</div>
              </div>
              <div className="card" style={{ padding: 14 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, marginBottom: 8 }} className="muted">
                  <Sparkles size={12} /> Genie
                  {!t.done && <span className="live-dot" style={{ marginLeft: 6 }} />}
                  {!t.done && <span>{STATUS_TEXT[t.status] || `${t.status}…`}</span>}
                </div>
                {t.error && <div style={{ color: 'var(--status-red)', fontSize: 12 }}>{t.error}</div>}
                {t.answer && <Answer a={t.answer} onAsk={ask} disabled={busy} />}
              </div>
            </div>
          ))}
          <div ref={endRef} />
        </div>
      </div>

      <form onSubmit={(e) => { e.preventDefault(); ask(input); }}
            className="panel" style={{ flexShrink: 0, borderRadius: 0, borderLeft: 'none', borderRight: 'none', borderBottom: 'none', padding: 12 }}>
        <div style={{ maxWidth: 980, margin: '0 auto', display: 'flex', gap: 8 }}>
          <input value={input} onChange={(e) => setInput(e.target.value)} disabled={busy}
                 placeholder={conversationId ? 'Ask a follow-up…' : 'Ask a question about flights, delays, airlines, gates…'}
                 aria-label="Question for Genie"
                 style={{ flex: 1, padding: '10px 12px', borderRadius: 8, border: '1px solid var(--border)',
                          background: 'var(--bg-card)', color: 'var(--text-primary)', fontSize: 13, outline: 'none' }} />
          <button type="submit" disabled={busy || !input.trim()} style={{ ...BTN, background: 'var(--accent-blue)', color: '#0a0e1a', fontWeight: 700 }}>
            <Send size={14} /> Ask
          </button>
        </div>
      </form>
    </div>
  );
}

function Answer({ a, onAsk, disabled }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {a.text && <div style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{a.text}</div>}
      {a.sql_description && !a.text && <div style={{ fontSize: 13, lineHeight: 1.6 }}>{a.sql_description}</div>}
      {a.columns?.length > 0 && <ResultTable a={a} />}
      {a.sql && (
        <details>
          <summary className="muted" style={{ cursor: 'pointer', fontSize: 11, display: 'flex', alignItems: 'center', gap: 5 }}>
            <Database size={12} /> Generated SQL
          </summary>
          <pre className="mono" style={{ margin: '8px 0 0', padding: 10, borderRadius: 8, fontSize: 11, lineHeight: 1.5,
                                         background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border-subtle)', overflowX: 'auto' }}>{a.sql}</pre>
        </details>
      )}
      {a.suggested_questions?.length > 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {a.suggested_questions.map((q) => (
            <button key={q} disabled={disabled} onClick={() => onAsk(q)} style={{ ...CHIP, padding: '5px 10px', fontSize: 11 }}>{q}</button>
          ))}
        </div>
      )}
    </div>
  );
}

function ResultTable({ a }) {
  return (
    <div>
      <div style={{ overflow: 'auto', maxHeight: 380, border: '1px solid var(--border-subtle)', borderRadius: 8 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>{a.columns.map((c) => (
              <th key={c} style={{ position: 'sticky', top: 0, background: 'var(--bg-elevated)', textAlign: 'left', padding: '7px 10px',
                                   fontSize: 10, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text-muted)',
                                   borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }}>{c}</th>
            ))}</tr>
          </thead>
          <tbody>
            {a.rows.map((r, i) => (
              <tr key={i}>{r.map((v, j) => (
                <td key={j} className={isNum(v) ? 'mono' : undefined}
                    style={{ padding: '6px 10px', borderBottom: '1px solid var(--border-subtle)', whiteSpace: 'nowrap',
                             textAlign: isNum(v) ? 'right' : 'left' }}>{v ?? <span className="muted">—</span>}</td>
              ))}</tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="muted" style={{ fontSize: 10.5, marginTop: 5 }}>
        {a.row_count ?? a.rows.length} row{(a.row_count ?? a.rows.length) === 1 ? '' : 's'}{a.truncated ? ` · showing first ${a.rows.length}` : ''}
      </div>
    </div>
  );
}

const isNum = (v) => v !== null && v !== '' && !Number.isNaN(Number(v));
const BTN = { display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 12px', borderRadius: 8, fontSize: 12,
              border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-primary)', cursor: 'pointer' };
const CHIP = { padding: '11px 13px', fontSize: 12.5, lineHeight: 1.45, cursor: 'pointer', color: 'var(--text-primary)',
               border: '1px solid var(--border)', borderRadius: 10, background: 'var(--bg-card)' };
