// Mission Control — stuck delivery cards grouped by what they need from the operator.
// Plain IIFE on the dashboard Plugin SDK; no build step.
(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  const h = SDK.React.createElement;
  const { useState, useEffect, useCallback } = SDK.hooks;
  const { Card, CardHeader, CardTitle, CardContent, Badge, Button, Input } = SDK.components;
  const API = "/api/plugins/mission-control";
  const post = (path, body) =>
    SDK.fetchJSON(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  function Detail({ id, onDone }) {
    const [card, setCard] = useState(null);
    const [note, setNote] = useState("");
    const [form, setForm] = useState(null);
    const [msg, setMsg] = useState("");
    useEffect(() => { SDK.fetchJSON(`${API}/cards/${id}`).then(setCard).catch((e) => setMsg(String(e))); }, [id]);
    if (!card) return h("div", { className: "mc-detail" }, msg || "Loading…");

    const act = (action) => {
      if (action === "archive" && !window.confirm(`Archive ${id}?`)) return;
      post("/act", { ids: [id], action, note }).then((r) => {
        const res = r.results[id];
        setMsg(res.ok ? `${action}: ${res.detail || "done"}` : `failed: ${res.detail}`);
        if (res.ok) onDone();
      }).catch((e) => setMsg(String(e)));
    };
    const resubmit = () => post(`/cards/${id}/resubmit`, form).then((r) => {
      setMsg(r.ok ? `Resubmitted as ${Object.values(r.cards).join(" → ")}` : `failed: ${r.error}`);
      if (r.ok) onDone();
    }).catch((e) => setMsg(String(e)));
    const field = (k) => (e) => setForm({ ...form, [k]: e.target.value });

    return h("div", { className: "mc-detail" },
      card.reason && h("p", { className: "mc-reason" }, card.reason),
      card.body && h("details", null, h("summary", null, "Card body"), h("pre", null, card.body)),
      card.comments.length > 0 && h("details", null, h("summary", null, `Latest comments (${card.comments.length})`),
        card.comments.map((c, i) => h("pre", { key: i }, `${c.author}: ${c.body}`))),
      !form && h("div", { className: "mc-act" },
        h("textarea", { className: "mc-note", placeholder: "Your decision or instruction (recorded on the card)",
          value: note, onChange: (e) => setNote(e.target.value) }),
        h("div", { className: "mc-row" },
          h(Button, { size: "sm", onClick: () => act("continue") }, "Continue"),
          h(Button, { size: "sm", outlined: true, onClick: () => setForm({ ...card.suggested, size: "small" }) }, "Resubmit fresh"),
          h(Button, { size: "sm", destructive: true, onClick: () => act("archive") }, "Archive"))),
      form && h("div", { className: "mc-act" },
        h(Input, { value: form.title, onChange: field("title"), placeholder: "Title" }),
        h("textarea", { className: "mc-note mc-brief", value: form.request, onChange: field("request") }),
        h("div", { className: "mc-row" },
          h(Input, { value: form.project, onChange: field("project"), placeholder: "Hermes project id" }),
          h("select", { className: "mc-select", value: form.size, onChange: field("size") },
            h("option", { value: "small" }, "small"), h("option", { value: "large" }, "large"))),
        h("small", { className: "mc-muted" }, `${form.request.length}/3000 — keep it a brief: goal, acceptance criteria, files. Old card is archived.`),
        h("div", { className: "mc-row" },
          h(Button, { size: "sm", onClick: resubmit }, "Submit"),
          h(Button, { size: "sm", ghost: true, onClick: () => setForm(null) }, "Cancel"))),
      msg && h("p", { className: "mc-muted" }, msg));
  }

  function MissionControl() {
    const [data, setData] = useState(null);
    const [open, setOpen] = useState(null);
    const [picked, setPicked] = useState({});
    const [error, setError] = useState("");
    const load = useCallback(() => {
      SDK.fetchJSON(`${API}/queue`).then((d) => { setData(d); setError(""); }).catch((e) => setError(String(e)));
    }, []);
    useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);

    const ids = Object.keys(picked).filter((k) => picked[k]);
    const bulk = (action) => {
      if (!window.confirm(`${action} ${ids.length} card(s)?`)) return;
      post("/act", { ids, action }).then((r) => {
        const failed = Object.entries(r.results).filter(([, v]) => !v.ok);
        if (failed.length) window.alert(failed.map(([k, v]) => `${k}: ${v.detail}`).join("\n"));
        setPicked({}); load();
      });
    };

    if (error) return h("p", { className: "mc-muted" }, error);
    if (!data) return h("p", { className: "mc-muted" }, "Loading…");
    return h("div", { className: "mc" },
      h("div", { className: "mc-row mc-top" },
        h("h2", null, "Mission Control"),
        h(Badge, { tone: data.total ? "destructive" : "secondary" }, `${data.total} not moving`),
        data.provider_walled && h(Badge, { tone: "outline" }, "provider rate limiting — retries paused"),
        h("span", { className: "mc-grow" }),
        ids.length > 0 && h(Button, { size: "sm", onClick: () => bulk("continue") }, `Continue ${ids.length}`),
        ids.length > 0 && h(Button, { size: "sm", destructive: true, onClick: () => bulk("archive") }, `Archive ${ids.length}`),
        h(Button, { size: "sm", outlined: true, onClick: load }, "Refresh")),
      data.total === 0 && h("p", { className: "mc-muted" }, "Nothing stuck. The board is moving."),
      data.groups.map((g) => h(Card, { key: g.key, className: "mc-group" },
        h(CardHeader, null,
          h(CardTitle, null, `${g.heading} (${g.cards.length})`),
          g.hint && h("small", { className: "mc-muted" }, g.hint.replace(/`/g, ""))),
        h(CardContent, null, g.cards.map((c) => h("div", { key: c.id, className: "mc-card" },
          h("div", { className: "mc-row mc-line", onClick: () => setOpen(open === c.id ? null : c.id) },
            h("input", { type: "checkbox", checked: !!picked[c.id], onClick: (e) => e.stopPropagation(),
              onChange: (e) => setPicked({ ...picked, [c.id]: e.target.checked }) }),
            h("code", null, c.id),
            h("span", { className: "mc-title" }, c.title),
            h(Badge, { tone: "outline" }, c.status),
            c.assignee && h("span", { className: "mc-muted" }, c.assignee),
            h("span", { className: "mc-muted" }, c.age)),
          open !== c.id && c.reason && h("div", { className: "mc-muted mc-clip" }, c.reason),
          open === c.id && h(Detail, { id: c.id, onDone: () => { setOpen(null); load(); } })))))));
  }

  window.__HERMES_PLUGINS__.register("mission-control", MissionControl);
})();
