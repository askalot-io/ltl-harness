/* LTL Guardrail Harness dashboard — no deps, no build step. */

const COLOR_LABEL = {
  PERM_SAT: 'permanently satisfied',
  CURR_SAT: 'currently satisfied',
  CURR_VIOL: 'currently violated (obligation open)',
  PERM_VIOL: 'permanently violated',
};

const S = {
  tab: 'runs',
  runs: [],
  selectedRun: null,
  records: [],
  recordCount: -1,
  viewStep: null, // null = latest; otherwise index into events
  rulebook: null,
};

/* ---------------- tiny DOM helper ---------------- */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    el.append(c.nodeType ? c : document.createTextNode(c));
  }
  return el;
}
const $ = (sel) => document.querySelector(sel);

/* ---------------- data ---------------- */
async function fetchJSON(url) {
  const res = await fetch(url);
  return res.json();
}

async function poll() {
  try {
    const runs = await fetchJSON('/api/runs');
    const changed = JSON.stringify(runs) !== JSON.stringify(S.runs);
    S.runs = runs;
    if (changed) renderRunList();
    if (!S.selectedRun && runs.length > 0) selectRun(runs[0].runId);
    else if (S.selectedRun) {
      const records = await fetchJSON('/api/runs/' + encodeURIComponent(S.selectedRun));
      if (Array.isArray(records) && records.length !== S.recordCount) {
        S.records = records;
        S.recordCount = records.length;
        if (S.tab === 'runs') renderMain();
      }
    }
  } catch { /* server restarting; retry next tick */ }
  setTimeout(poll, 1200);
}

function selectRun(runId) {
  S.selectedRun = runId;
  S.records = [];
  S.recordCount = -1;
  S.viewStep = null;
  S.tab = 'runs';
  syncTabs();
  renderRunList();
  fetchJSON('/api/runs/' + encodeURIComponent(runId)).then((records) => {
    S.records = records;
    S.recordCount = records.length;
    renderMain();
  });
}

/* ---------------- derived views of a run ---------------- */
function runParts() {
  const recs = S.records;
  return {
    start: recs.find((r) => r.type === 'run_start'),
    events: recs.filter((r) => r.type === 'event'),
    end: recs.find((r) => r.type === 'run_end'),
    finalReply: recs.find((r) => r.type === 'final_reply'),
  };
}

/** rule states at event index i (or initial states for i === -1). */
function statesAt(parts, i) {
  if (i >= 0 && parts.events[i]) return parts.events[i].states;
  const out = {};
  for (const rule of parts.start.rulebook.rules) {
    out[rule.id] = { state: rule.machine.initial, color: colorOf(rule.machine, rule.machine.initial) };
  }
  return out;
}
function colorOf(machine, stateId) {
  return machine.states.find((s) => s.id === stateId)?.color;
}
function currentEventIndex(parts) {
  return S.viewStep === null ? parts.events.length - 1 : S.viewStep;
}

/* ---------------- run list ---------------- */
function renderRunList() {
  const list = $('#run-list');
  list.replaceChildren();
  if (S.runs.length === 0) {
    list.append(h('div', { class: 'empty' }, 'no runs yet — try:', h('br'), h('code', {}, 'python -m ltl_harness.agent happy')));
    return;
  }
  for (const run of S.runs) {
    const m = run.runId.match(/_(\w+)$/);
    const scenario = m ? m[1] : run.runId;
    const time = new Date(run.mtime).toLocaleTimeString();
    list.append(
      h('div', {
        class: 'run-item' + (run.runId === S.selectedRun ? ' selected' : ''),
        onclick: () => selectRun(run.runId),
      },
        h('div', { class: 'run-scenario' }, scenario, badgeFor(run.runId)),
        h('div', { class: 'run-time mono' }, run.runId.slice(0, 19) + ' · ' + time),
      ),
    );
  }
}

const badgeCache = {};
function badgeFor(runId) {
  // badge from last known data if this is the selected run; otherwise cached
  if (runId === S.selectedRun && S.records.length) {
    const end = S.records.find((r) => r.type === 'run_end');
    badgeCache[runId] = !end ? 'running' : end.ok ? 'ok' : 'bad';
  }
  const kind = badgeCache[runId];
  if (!kind) return null;
  const text = kind === 'running' ? 'RUNNING' : kind === 'ok' ? 'COMPLIANT' : 'VIOLATED';
  return h('span', { class: 'badge ' + kind }, text);
}

/* ---------------- main: run view ---------------- */
function renderMain() {
  const main = $('#main');
  if (S.tab === 'rulebook') return renderRulebook(main);
  main.replaceChildren();
  const parts = runParts();
  if (!parts.start) {
    main.append(h('div', { class: 'empty' }, 'select a run — or start one: ', h('code', {}, 'python -m ltl_harness.agent rogue')));
    return;
  }

  main.append(renderVerdictBanner(parts));
  main.append(h('div', { class: 'customer-msg' }, '“' + parts.start.customerMessage + '”'));

  main.append(h('h2', {}, 'Rule status over the trace',
    h('span', { class: 'hint' }, 'each row is one rule; each column one trace event — click to time-travel')));
  main.append(renderMatrix(parts));
  main.append(renderLegend());

  main.append(h('h2', {}, 'State machines',
    h('span', { class: 'hint' },
      S.viewStep === null ? 'live — showing latest state' : `showing state after event #${S.viewStep + 1}`),
    S.viewStep !== null
      ? h('button', { class: 'linkish', style: 'margin-left:12px', onclick: () => { S.viewStep = null; renderMain(); } }, '⏵ back to live')
      : null,
  ));
  main.append(renderMachines(parts));

  main.append(h('h2', {}, 'Flight recorder', h('span', { class: 'hint' }, 'every event, denial, and stop-check')));
  main.append(renderTimeline(parts));
}

function renderVerdictBanner(parts) {
  const { start, end } = parts;
  const denials = S.records.filter((r) => r.type === 'denial').length;
  if (!end) {
    return h('div', { class: 'verdict-banner' },
      h('span', { class: 'big' }, '⏳'),
      h('div', {},
        h('div', {}, h('b', {}, start.scenarioLabel ?? start.scenario), ' — running…'),
        h('div', { class: 'mono', style: 'color:var(--muted);font-size:12px' }, start.runId)));
  }
  const violated = Object.entries(end.verdicts).filter(([, v]) => v.verdict === 'VIOLATED');
  return h('div', { class: 'verdict-banner ' + (end.ok ? 'ok' : 'bad') },
    h('span', { class: 'big' }, end.ok ? '✓' : '✗'),
    h('div', {},
      h('div', {}, h('b', {}, start.scenarioLabel ?? start.scenario)),
      h('div', {},
        end.ok
          ? `run complies with the rulebook (${denials ? denials + ' attempt(s) denied by the seatbelt' : 'no denials needed'})`
          : 'violated at end of trace: ' + violated.map(([id]) => id).join(', ')),
    ));
}

/* ---------------- matrix ---------------- */
function renderMatrix(parts) {
  const rules = parts.start.rulebook.rules;
  const cols = S.records.filter((r) => r.type === 'event' || r.type === 'denial');
  let eventIdx = -1;

  const headerCells = [h('th', {}, '')];
  const bodyRows = rules.map((rule) => [h('td', { class: 'rule-name mono' }, rule.id)]);
  const selected = currentEventIndex(parts);

  for (const rec of cols) {
    if (rec.type === 'event') {
      eventIdx += 1;
      const i = eventIdx;
      headerCells.push(h('th', {
        class: 'step-h' + (i === selected ? ' selected' : ''),
        onclick: () => { S.viewStep = i; renderMain(); },
        title: `event #${i + 1}: ${rec.activity}`,
      }, h('div', { class: 'lbl mono' }, rec.activity)));
      rules.forEach((rule, ri) => {
        const cell = rec.states[rule.id];
        bodyRows[ri].push(h('td', {
          class: 'cell' + (i === selected ? ' selected' : ''),
          onclick: () => { S.viewStep = i; renderMain(); },
          title: `${rule.id} after '${rec.activity}': ${COLOR_LABEL[cell.color]} (state ${cell.state})`,
        }, h('div', { class: 'sw ' + cell.color })));
      });
    } else {
      headerCells.push(h('th', { class: 'step-h', title: rec.reason }, h('div', { class: 'lbl mono', style: 'color:var(--perm-viol)' }, '⛔ ' + rec.tool.replace(/^mcp__refund__/, ''))));
      rules.forEach((rule, ri) => {
        bodyRows[ri].push(h('td', { class: 'denial-cell', title: rec.reason },
          rec.deniedBy.includes(rule.id) ? '⛔' : ''));
      });
    }
  }

  return h('div', { class: 'matrix-wrap' },
    h('table', { class: 'matrix' },
      h('thead', {}, h('tr', {}, headerCells)),
      h('tbody', {}, bodyRows.map((cells) => h('tr', {}, cells)))));
}

function renderLegend() {
  return h('div', { class: 'legend' },
    ...Object.entries(COLOR_LABEL).map(([color, label]) =>
      h('span', {}, h('span', { class: 'sw ' + color }), label)),
    h('span', {}, '⛔ denied by seatbelt (event never happened)'),
  );
}

/* ---------------- state machine rendering ---------------- */
function renderMachines(parts) {
  const rules = parts.start.rulebook.rules;
  const idx = currentEventIndex(parts);
  const states = statesAt(parts, idx);
  const prevStates = statesAt(parts, idx - 1);
  const activity = idx >= 0 && parts.events[idx] ? parts.events[idx].activity : null;

  return h('div', { class: 'fsm-grid' },
    rules.map((rule) => {
      const cur = states[rule.id];
      return h('div', { class: 'fsm-card ' + cur.color },
        h('span', { class: 'posture' }, rule.posture),
        h('div', { class: 'fsm-title' }, rule.id, ' ', h('span', { style: 'color:var(--muted);font-weight:400' }, `(${rule.template})`)),
        h('div', { class: 'fsm-desc' }, rule.description),
        h('div', { class: 'fsm-formula mono' }, rule.machine.formula),
        renderFSM(rule.machine, cur.state, { prevState: prevStates[rule.id]?.state, activity }),
        h('div', { style: 'font-size:11px;color:var(--muted);margin-top:4px' },
          'now: ', h('b', { style: 'color:var(--text)' }, cur.state), ' — ' + COLOR_LABEL[cur.color]),
      );
    }));
}

/** Layout: BFS layers left-to-right. Returns an <svg>. */
function renderFSM(machine, currentState, { prevState, activity } = {}) {
  const NS = 'http://www.w3.org/2000/svg';
  const R = 20, LX = 130, LY = 78, PAD = 42;

  // successor map incl. "other" edges
  const succ = new Map(machine.states.map((s) => [s.id, []]));
  for (const e of machine.edges) succ.get(e.from).push({ to: e.to, label: e.on.join(' | '), other: false });
  for (const [from, to] of Object.entries(machine.otherEdges)) succ.get(from).push({ to, label: 'other', other: true });

  // BFS layers
  const depth = new Map([[machine.initial, 0]]);
  const queue = [machine.initial];
  while (queue.length) {
    const id = queue.shift();
    for (const { to } of succ.get(id)) {
      if (!depth.has(to)) { depth.set(to, depth.get(id) + 1); queue.push(to); }
    }
  }
  machine.states.forEach((s) => { if (!depth.has(s.id)) depth.set(s.id, 0); });

  const byLayer = new Map();
  for (const s of machine.states) {
    const d = depth.get(s.id);
    if (!byLayer.has(d)) byLayer.set(d, []);
    byLayer.get(d).push(s.id);
  }
  const pos = new Map();
  let maxRows = 1;
  for (const [d, ids] of byLayer) {
    maxRows = Math.max(maxRows, ids.length);
    ids.forEach((id, i) => pos.set(id, { x: PAD + 20 + d * LX, y: PAD + i * LY }));
  }
  const width = PAD * 2 + 20 + (Math.max(...depth.values()) * LX) + R * 2 + 30;
  const height = PAD * 2 + (maxRows - 1) * LY + 20;

  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

  // arrow markers
  svg.innerHTML =
    `<defs>
      <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker>
      <marker id="arrow-taken" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker>
    </defs>`;

  // edges (group both directions to curve nicely)
  for (const s of machine.states) {
    for (const e of succ.get(s.id)) {
      const p1 = pos.get(s.id), p2 = pos.get(e.to);
      const g = document.createElementNS(NS, 'g');
      const reverseExists = succ.get(e.to)?.some((r) => r.to === s.id);
      const taken = prevState === s.id && currentState === e.to && prevState !== currentState &&
        (e.other ? true : activity !== null && e.label.split(' | ').includes(activity));
      g.setAttribute('class', 'fsm-edge' + (e.other ? ' other' : '') + (taken ? ' taken' : ''));

      const dx = p2.x - p1.x, dy = p2.y - p1.y;
      const len = Math.hypot(dx, dy) || 1;
      const ux = dx / len, uy = dy / len;
      const sx = p1.x + ux * R, sy = p1.y + uy * R;
      const ex = p2.x - ux * (R + 4), ey = p2.y - uy * (R + 4);
      const bend = reverseExists ? 18 : (dy === 0 && Math.abs(dx) > LX ? 26 : 0);
      const mx = (sx + ex) / 2 - uy * bend, my = (sy + ey) / 2 + ux * bend;

      const path = document.createElementNS(NS, 'path');
      path.setAttribute('d', `M ${sx} ${sy} Q ${mx} ${my} ${ex} ${ey}`);
      path.setAttribute('marker-end', taken ? 'url(#arrow-taken)' : 'url(#arrow)');
      g.append(path);

      const label = document.createElementNS(NS, 'text');
      label.setAttribute('x', mx); label.setAttribute('y', my - 5);
      label.textContent = e.label;
      g.append(label);
      svg.append(g);
    }
  }

  // initial-state entry arrow
  const init = pos.get(machine.initial);
  const entry = document.createElementNS(NS, 'g');
  entry.setAttribute('class', 'fsm-edge');
  entry.innerHTML = `<path d="M ${init.x - R - 26} ${init.y} L ${init.x - R - 5} ${init.y}" marker-end="url(#arrow)"/>`;
  svg.append(entry);

  // nodes
  for (const s of machine.states) {
    const p = pos.get(s.id);
    const g = document.createElementNS(NS, 'g');
    g.setAttribute('class', `fsm-node ${s.color}` + (s.id === currentState ? ' current' : ''));
    const circle = document.createElementNS(NS, 'circle');
    circle.setAttribute('cx', p.x); circle.setAttribute('cy', p.y); circle.setAttribute('r', R);
    g.append(circle);
    const t1 = document.createElementNS(NS, 'text');
    t1.setAttribute('x', p.x); t1.setAttribute('y', p.y + 3.5);
    t1.textContent = s.id;
    g.append(t1);
    const t2 = document.createElementNS(NS, 'text');
    t2.setAttribute('x', p.x); t2.setAttribute('y', p.y + R + 12);
    t2.setAttribute('class', 'state-label');
    t2.textContent = s.label;
    g.append(t2);
    const title = document.createElementNS(NS, 'title');
    title.textContent = `${s.id}: ${COLOR_LABEL[s.color]}`;
    g.append(title);
    svg.append(g);
  }
  return svg;
}

/* ---------------- timeline ---------------- */
function renderTimeline(parts) {
  const items = [];
  let eventIdx = -1;
  for (const rec of S.records) {
    if (rec.type === 'event') {
      eventIdx += 1;
      const i = eventIdx;
      items.push(h('div', {
        class: 't-item' + (i === currentEventIndex(parts) && S.viewStep !== null ? ' selected' : ''),
        onclick: () => { S.viewStep = i; renderMain(); },
      },
        h('span', { class: 't-step mono' }, `#${i + 1}`),
        h('span', { class: 't-act mono' }, rec.activity),
        h('span', { class: 't-detail mono' }, JSON.stringify(rec.input), ' → ', JSON.stringify(rec.result)),
        rec.alerts?.map((a) => h('span', { class: 'alert' }, `⚠ ${a.rule} went red`)),
      ));
    } else if (rec.type === 'denial') {
      items.push(h('div', { class: 't-item denial' },
        h('span', { class: 't-step mono' }, '⛔'),
        h('span', { class: 't-act mono' }, rec.tool.replace(/^mcp__refund__/, '') + ' DENIED'),
        h('span', { class: 't-detail' }, rec.reason),
      ));
    } else if (rec.type === 'stop_check' && rec.blocked) {
      items.push(h('div', { class: 't-item stop-block' },
        h('span', { class: 't-step mono' }, '✋'),
        h('span', { class: 't-act' }, 'stop blocked'),
        h('span', { class: 't-detail' }, 'open obligations: ' + rec.openObligations.map((o) => o.id).join(', ')),
      ));
    } else if (rec.type === 'final_reply' && rec.text) {
      items.push(h('div', { class: 't-item final-reply' },
        h('span', { class: 't-step mono' }, '💬'),
        h('span', { class: 't-detail' }, rec.text),
      ));
    }
  }
  return h('div', { class: 'timeline' }, items);
}

/* ---------------- rulebook view ---------------- */
async function renderRulebook(main) {
  main.replaceChildren(h('div', { class: 'empty' }, 'loading rulebook…'));
  const rb = S.rulebook ?? (S.rulebook = await fetchJSON('/api/rulebook'));
  main.replaceChildren();
  if (rb.error) {
    main.append(h('div', { class: 'lint-box bad' }, 'rulebook does not parse: ' + rb.error));
    return;
  }

  const lint = rb.lint;
  main.append(h('h2', {}, `Rulebook: ${rb.name} (v${rb.version})`));
  main.append(h('div', { class: 'lint-box ' + (lint.ok ? 'ok' : 'bad') },
    h('div', {}, h('b', {}, lint.ok ? '✓ design-time verification passed' : '✗ design-time verification FAILED'),
      h('span', { style: 'color:var(--muted);margin-left:10px;font-size:12px' }, `${lint.productStates} product states explored`)),
    lint.findings.length === 0
      ? h('div', { style: 'color:var(--muted);font-size:12px' }, 'no contradictions, traps, dead rules, or shadowed rules')
      : lint.findings.map((f) => h('div', { class: 'lint-finding ' + f.severity },
          h('span', { class: 'kind' }, f.kind), f.message,
          f.trace ? h('div', { class: 'mono', style: 'color:var(--muted)' }, 'counterexample: ' + f.trace.join(' → ')) : null)),
  ));

  main.append(h('h2', {}, 'Rules', h('span', { class: 'hint' }, 'each rule is a little machine')));
  main.append(h('div', { class: 'fsm-grid' },
    rb.rules.map((rule) => h('div', { class: 'fsm-card' },
      h('span', { class: 'posture' }, rule.posture),
      h('div', { class: 'fsm-title' }, rule.id, ' ', h('span', { style: 'color:var(--muted);font-weight:400' }, `(${rule.template})`)),
      h('div', { class: 'fsm-desc' }, rule.description),
      h('div', { class: 'fsm-formula mono' }, rule.machine.formula),
      renderFSM(rule.machine, rule.machine.initial),
    ))));

  main.append(h('h2', {}, 'Activities', h('span', { class: 'hint' }, 'the world-to-symbols mapping — audit it like a security boundary')));
  main.append(h('table', { class: 'act-table' },
    h('tr', {}, h('th', {}, 'activity'), h('th', {}, 'tool'), h('th', {}, 'result condition')),
    Object.entries(rb.activities).map(([name, spec]) =>
      h('tr', {},
        h('td', { class: 'mono' }, name),
        h('td', { class: 'mono' }, spec.tool),
        h('td', { class: 'mono' }, spec.result ? `${spec.result.path} == ${JSON.stringify(spec.result.equals)}` : '—'))),
  ));
}

/* ---------------- tabs + boot ---------------- */
function syncTabs() {
  $('#tab-runs').classList.toggle('active', S.tab === 'runs');
  $('#tab-rulebook').classList.toggle('active', S.tab === 'rulebook');
}
$('#tab-runs').addEventListener('click', () => { S.tab = 'runs'; syncTabs(); renderMain(); });
$('#tab-rulebook').addEventListener('click', () => { S.tab = 'rulebook'; S.rulebook = null; syncTabs(); renderMain(); });

fetchJSON('/api/rulebook').then((rb) => {
  const pill = $('#rulebook-pill');
  if (rb.error) { pill.textContent = 'rulebook: parse error'; pill.classList.add('bad'); return; }
  pill.textContent = `rulebook: ${rb.name} v${rb.version} — lint ${rb.lint.ok ? '✓' : '✗'}`;
  pill.classList.add(rb.lint.ok ? 'ok' : 'bad');
});

poll();
