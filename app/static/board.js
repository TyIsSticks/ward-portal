// Ministering board: districts -> groups, each with Ministers and Assigned drop zones.
// Plain JS, no dependencies. Every change re-renders and autosaves the whole layout.
(() => {
  "use strict";

  const S = JSON.parse(document.getElementById("state").textContent);
  const RULES = { minMinisters: 2, maxMinisters: 3, maxAssigned: 6 };
  const NEEDS = new Set(S.needs_ministers_tags);
  const GENDER_WORD = { M: "brother", F: "sister" };

  // Which people each side list shows: "all", "M" or "F". Defaults come from the organization
  // (who ministers in it, and who its current import assigns); a leader's choice is remembered.
  const scopes = { minister: S.minister_gender, assigned: S.assigned_scope };
  for (const pool of Object.keys(scopes)) {
    try { scopes[pool] = localStorage.getItem(`pool-scope-${S.org}-${pool}`) || scopes[pool]; } catch {}
  }
  const inScope = (pool, pid) => scopes[pool] === "all" || person(pid).gender === scopes[pool];

  let uidN = 0;
  const uid = () => "k" + (++uidN);
  let districts = S.districts.map(d => ({
    key: uid(), name: d.name, supervisor: d.supervisor || "",
    groups: d.groups.map(g => ({ key: uid(), ministers: [...g.ministers], assigned: [...g.assigned] })),
  }));
  let version = S.layout.version;
  const people = S.people;            // keyed by id (string)
  const $ = (sel, root = document) => root.querySelector(sel);
  const elDistricts = $("#districts");
  const elPop = $("#popover");
  let query = "";

  const person = (pid) => people[pid] || { id: pid, display: "Unknown", name: "Unknown", gender: null, active: true, tags: [] };
  const byName = (a, b) => person(a).name.localeCompare(person(b).name);
  const ro = () => S.layout.readonly;
  const roleKey = (role) => (role === "minister" ? "ministers" : "assigned");

  // --- tiny DOM helper (always textContent, never innerHTML, for names) -----
  function h(tag, attrs = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid);
    return el;
  }

  // --- derived lookups --------------------------------------------------------
  let idx;
  function reindex() {
    idx = { group: new Map(), minister: new Map(), assigned: new Map() };
    districts.forEach((d, di) => d.groups.forEach((g, gi) => {
      idx.group.set(g.key, { g, d, di, gi });
      for (const p of g.ministers) (idx.minister.get(p) || idx.minister.set(p, []).get(p)).push(g.key);
      for (const p of g.assigned) (idx.assigned.get(p) || idx.assigned.set(p, []).get(p)).push(g.key);
    }));
  }
  const groupLabel = (gkey) => {
    const x = idx.group.get(gkey);
    return x ? `${x.d.name} · Group ${x.gi + 1}` : "";
  };
  const activeIds = () => Object.keys(people).map(Number).filter(p => people[p].active);

  function companionHistory(a, b) {
    const k = a < b ? `${a}-${b}` : `${b}-${a}`;
    return S.history.companions[k] || null;
  }
  const ministeredHistory = (m, p) => S.history.ministered[`${m}-${p}`] || null;

  // --- mutations --------------------------------------------------------------
  function move(pid, role, fromKey, toKey) {
    if (ro() || fromKey === toKey) return;
    const key = roleKey(role);
    if (fromKey) {
      const g = idx.group.get(fromKey)?.g;
      if (g) g[key] = g[key].filter(p => p !== pid);
    }
    if (toKey) {
      const g = idx.group.get(toKey)?.g;
      if (g && !g[key].includes(pid)) g[key].push(pid);
    }
    changed();
  }

  function changed() {
    render();
    scheduleSave();
  }

  // --- saving -----------------------------------------------------------------
  let saveTimer = null, saving = false, dirty = false;
  const elSave = $("#save-state");
  function scheduleSave() {
    dirty = true;
    elSave.textContent = "Unsaved changes…";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 700);
  }
  async function save() {
    if (saving) { saveTimer = setTimeout(save, 300); return; }
    saving = true; dirty = false;
    elSave.textContent = "Saving…";
    const body = {
      version,
      districts: districts.map(d => ({ name: d.name, supervisor: d.supervisor,
        groups: d.groups.map(g => ({ ministers: g.ministers, assigned: g.assigned })) })),
    };
    try {
      const res = await fetch(`/api/ministering/layouts/${S.layout.id}/structure`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (res.status === 409) {
        $("#conflict").hidden = false;
        S.layout.readonly = true;
        elSave.textContent = "Not saved";
        render();
      } else if (!res.ok) {
        elSave.textContent = "Couldn't save. Check your connection.";
        dirty = true;
      } else {
        version = (await res.json()).version;
        elSave.textContent = dirty ? "Unsaved changes…" : "All changes saved";
      }
    } catch {
      elSave.textContent = "Couldn't save. Check your connection.";
      dirty = true;
    } finally {
      saving = false;
    }
  }
  window.addEventListener("beforeunload", (e) => { if (dirty || saving) { e.preventDefault(); e.returnValue = ""; } });

  async function patchMeta(data) {
    const res = await fetch(`/api/ministering/layouts/${S.layout.id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    if (!res.ok) { elSave.textContent = "Couldn't save."; return null; }
    const out = await res.json();
    S.layout.readonly = out.readonly;
    S.layout.status = out.status;
    elSave.textContent = "All changes saved";
    render();
    return out;
  }

  // --- rendering --------------------------------------------------------------
  function chip(pid, role, gkey) {
    const p = person(pid);
    const cls = ["chip", `g-${p.gender || "U"}`];
    if (!p.active) cls.push("moved");
    if (p.tags.length) cls.push("tagged");
    let hist = null;
    if (role === "assigned" && gkey) {
      const g = idx.group.get(gkey).g;
      const prior = g.ministers.filter(m => ministeredHistory(m, pid));
      if (prior.length) hist = `Ministered to by ${prior.map(m => person(m).display).join(", ")} before`;
    }
    const titleBits = [p.display, p.gender === "M" ? "Brother" : p.gender === "F" ? "Sister" : null,
                       p.age ? `${p.age}` : null, p.tags.join(", ") || null, !p.active ? "Moved out" : null, hist];
    return h("button", {
      type: "button", class: cls.join(" "), title: titleBits.filter(Boolean).join(" · "),
      dataset: { pid, role, gkey: gkey || "" },
    },
      h("span", { class: "nm" }, p.display),
      p.tags.length ? h("span", { class: "mark", "aria-hidden": "true" }, "★") : null,
      hist ? h("span", { class: "mark", "aria-hidden": "true" }, "↺") : null);
  }

  function zone(role, g) {
    const ids = g[roleKey(role)];
    return h("div", { class: `zone ${role}`, dataset: { role, gkey: g.key } },
      h("div", { class: "zone-label" }, role === "minister" ? "Ministers" : `Assigned (${ids.length})`),
      ...ids.map(p => chip(p, role, g.key)),
      ids.length ? null : h("div", { class: "zone-empty" }, ro() ? "None" : "Drop names here"));
  }

  function groupWarnings(g) {
    const out = [];
    const m = g.ministers.length, a = g.assigned.length;
    if (m === 0) out.push("No ministers");
    else if (m < RULES.minMinisters) out.push("Only 1 minister");
    else if (m > RULES.maxMinisters) out.push(`${m} ministers`);
    if (a > RULES.maxAssigned) out.push(`${a} assigned (more than ${RULES.maxAssigned})`);
    if (m && a === 0) out.push("No one assigned");
    const genders = new Set(g.ministers.map(p => person(p).gender).filter(Boolean));
    if (genders.size > 1) out.push("Brothers and sisters as companions");
    for (const p of g.ministers) {
      const gd = person(p).gender;
      if (gd && gd !== S.minister_gender) out.push(`${person(p).display} is a ${GENDER_WORD[gd]} in ${S.org_name}`);
    }
    for (const p of g.ministers) if (g.assigned.includes(p)) out.push(`${person(p).display} is assigned to their own group`);
    for (const p of [...g.ministers, ...g.assigned]) if (!person(p).active) out.push(`${person(p).display} moved out`);
    return out;
  }

  function renderGroup(g, d, gi) {
    const warns = groupWarnings(g);
    const notes = [];
    const ms = g.ministers;
    for (let i = 0; i < ms.length; i++) for (let j = i + 1; j < ms.length; j++) {
      const hst = companionHistory(ms[i], ms[j]);
      if (hst) notes.push(`↺ ${person(ms[i]).display} & ${person(ms[j]).display} were companions in ${hst[hst.length - 1]}`);
    }
    return h("article", { class: "group" + (warns.length ? " has-warn" : ""), id: `g-${g.key}`, dataset: { gkey: g.key } },
      h("header", {},
        h("span", { class: "g-title" }, `Group ${gi + 1}`),
        warns.length ? h("span", { class: "g-warn", title: warns.join("\n") }, `⚠ ${warns.length}`) : null,
        ro() ? null : h("button", { type: "button", class: "icon", "aria-label": `Group ${gi + 1} options`,
          onclick: (e) => openGroupMenu(g, e.currentTarget) }, "⋯")),
      zone("minister", g),
      notes.length ? h("div", { class: "hist-note" }, notes.join(" · ")) : null,
      zone("assigned", g));
  }

  function renderDistricts() {
    const { scrollLeft, scrollTop } = elDistricts;
    elDistricts.replaceChildren(
      ...districts.map((d, di) => {
        const assigned = d.groups.reduce((n, g) => n + g.assigned.length, 0);
        const ministers = d.groups.reduce((n, g) => n + g.ministers.length, 0);
        return h("section", { class: "district", dataset: { dkey: d.key } },
          h("header", {},
            h("input", { class: "d-name", value: d.name, readonly: ro(), "aria-label": "District name", maxlength: 80,
              onchange: (e) => { d.name = e.target.value.trim() || d.name; changed(); } }),
            h("input", { class: "d-sup", value: d.supervisor, readonly: ro(), placeholder: "Supervisor",
              "aria-label": "District supervisor", maxlength: 80,
              onchange: (e) => { d.supervisor = e.target.value.trim(); changed(); } }),
            h("div", { class: "d-count muted small" },
              `${d.groups.length} groups · ${ministers} ministers · ${assigned} assigned`,
              ro() ? null : h("button", { type: "button", class: "link danger small", onclick: () => deleteDistrict(d) }, "Delete"))),
          ...d.groups.map((g, gi) => renderGroup(g, d, gi)),
          ro() ? null : h("button", { type: "button", class: "add", onclick: () => {
            d.groups.push({ key: uid(), ministers: [], assigned: [] }); changed(); } }, "+ Add group"));
      }),
      ro() ? null : h("button", { type: "button", class: "add add-district", onclick: () => {
        districts.push({ key: uid(), name: `District ${districts.length + 1}`, supervisor: "", groups: [] });
        changed();
      } }, "+ Add district"));
    elDistricts.scrollLeft = scrollLeft;
    elDistricts.scrollTop = scrollTop;
  }

  function renderPools() {
    const active = activeIds();
    const unassigned = active.filter(p => !idx.assigned.has(p) && inScope("assigned", p)).sort(byName);
    const nonMin = active.filter(p => !idx.minister.has(p) && inScope("minister", p)).sort(byName);
    const fill = (el, ids, role, empty) => el.replaceChildren(
      ...ids.map(p => chip(p, role, null)), ids.length ? "" : h("div", { class: "zone-empty" }, empty));
    fill($("#pool-assigned"), unassigned, "assigned", "Everyone is assigned 🎉");
    fill($("#pool-minister"), nonMin, "minister", "Everyone is ministering");
    $("#n-unassigned").textContent = unassigned.length;
    $("#n-nonminister").textContent = nonMin.length;
  }

  function computeWarnings() {
    const list = [];
    districts.forEach(d => d.groups.forEach((g, gi) => {
      for (const w of groupWarnings(g)) list.push({ text: `${d.name} · Group ${gi + 1}: ${w}`, gkey: g.key });
    }));
    for (const [p, gs] of idx.minister) if (gs.length > 1) list.push({ text: `${person(p).display} is ministering in ${gs.length} groups`, pid: p });
    for (const [p, gs] of idx.assigned) if (gs.length > 1) list.push({ text: `${person(p).display} is assigned to ${gs.length} groups`, pid: p });
    for (const p of activeIds()) {
      if (!inScope("assigned", p)) continue;
      const t = person(p).tags.filter(x => NEEDS.has(x));
      if (t.length && !idx.assigned.has(p)) list.push({ text: `${person(p).display} (${t[0]}) isn't assigned to anyone`, pid: p });
    }
    const totals = districts.filter(d => d.groups.length).map(d => [d.name, d.groups.reduce((n, g) => n + g.assigned.length, 0)]);
    if (totals.length > 1) {
      const nums = totals.map(t => t[1]), max = Math.max(...nums), min = Math.min(...nums);
      if (max - min >= 4 && max > min * 1.3) list.push({ text: `Districts are uneven: ${totals.map(t => `${t[0]} ${t[1]}`).join(", ")}`, info: true });
    }
    return list;
  }

  function renderWarnings() {
    const list = computeWarnings();
    $("#warn-count").textContent = list.filter(w => !w.info).length || "";
    $("#warnings").replaceChildren(...(list.length ? list.map(w => h("li", { class: w.info ? "info" : "" },
      h("button", { type: "button", class: "link", onclick: () => focusWarning(w) }, w.text)))
      : [h("li", { class: "muted" }, "No warnings")]));
  }

  function focusWarning(w) {
    if (w.gkey) flashEl(document.getElementById(`g-${w.gkey}`));
    else if (w.pid) {
      const el = document.querySelector(`.chip[data-pid="${w.pid}"]`);
      if (el) { flashEl(el); openPerson(el); }
    }
  }
  function flashEl(el) {
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
    el.classList.remove("flash-hl"); void el.offsetWidth; el.classList.add("flash-hl");
  }

  function applySearch() {
    document.body.classList.toggle("searching", !!query);
    let first = null;
    for (const el of document.querySelectorAll(".chip")) {
      const hit = !!query && person(el.dataset.pid).name.toLowerCase().includes(query)
               || !!query && person(el.dataset.pid).display.toLowerCase().includes(query);
      el.classList.toggle("hit", hit);
      if (hit && !first && el.dataset.gkey) first = el;
    }
    return first;
  }

  function renderReadonlyNote() {
    const note = $("#readonly-note");
    const conflict = !$("#conflict").hidden;
    note.hidden = !ro() || conflict;
    if (S.layout.kind === "imported") $("#readonly-text").textContent =
      "This is a snapshot imported from LCR, so it can't be edited.";
    else if (S.layout.status === "approved") $("#readonly-text").textContent =
      "This layout is approved and locked. Set the status back to Draft to edit it, or";
    document.body.classList.toggle("readonly", ro());
  }

  function render() {
    reindex();
    renderDistricts();
    renderPools();
    renderWarnings();
    renderReadonlyNote();
    applySearch();
  }

  // --- popovers ---------------------------------------------------------------
  let popAnchor = null;
  function showPop(anchor, ...content) {
    popAnchor = anchor;
    elPop.replaceChildren(h("button", { type: "button", class: "icon close", "aria-label": "Close", onclick: closePop }, "✕"),
      ...content.filter(c => c != null && c !== false));
    elPop.hidden = false;
    const r = anchor.getBoundingClientRect();
    if (window.innerWidth < 640) { elPop.style.left = elPop.style.top = ""; return; }  // CSS bottom sheet
    const w = elPop.offsetWidth, ht = elPop.offsetHeight;
    let left = Math.min(r.left, window.innerWidth - w - 12);
    let top = r.bottom + 6;
    if (top + ht > window.innerHeight - 12) top = Math.max(12, r.top - ht - 6);
    elPop.style.left = `${Math.max(12, left)}px`;
    elPop.style.top = `${top}px`;
  }
  function closePop() { elPop.hidden = true; popAnchor = null; }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });
  document.addEventListener("pointerdown", (e) => {
    if (!elPop.hidden && !elPop.contains(e.target) && e.target !== popAnchor) closePop();
  });

  function openPerson(el) {
    const pid = Number(el.dataset.pid), role = el.dataset.role, gkey = el.dataset.gkey || null;
    const p = person(pid);
    const facts = [p.gender === "M" ? "Brother" : p.gender === "F" ? "Sister" : null, p.age ? `Age ${p.age}` : null,
                   !p.active ? `Moved out ${(p.left_at || "").slice(0, 10)}` : null].filter(Boolean).join(" · ");

    // Where they are now.
    const where = [];
    for (const g of idx.minister.get(pid) || []) where.push(`Ministers in ${groupLabel(g)}`);
    for (const g of idx.assigned.get(pid) || []) where.push(`Assigned to ${groupLabel(g)}`);

    // History from past imports.
    const hist = [];
    for (const [k, v] of Object.entries(S.history.companions)) {
      const [a, b] = k.split("-").map(Number);
      if (a === pid || b === pid) hist.push(`Companions with ${person(a === pid ? b : a).display} (${v[v.length - 1]})`);
    }
    for (const [k, v] of Object.entries(S.history.ministered)) {
      const [m, a] = k.split("-").map(Number);
      if (a === pid) hist.push(`Ministered to by ${person(m).display} (${v[v.length - 1]})`);
    }

    const tagBox = h("div", { class: "tags" }, ...S.tags.map(t => h("button", {
      type: "button", class: "tag" + (p.tags.includes(t) ? " on" : ""), "aria-pressed": String(p.tags.includes(t)),
      onclick: async (e) => {
        const btn = e.currentTarget;
        const next = p.tags.includes(t) ? p.tags.filter(x => x !== t) : [...p.tags, t];
        const res = await fetch(`/api/ministering/people/${pid}/tags`, {
          method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tags: next }) });
        if (!res.ok) return;
        p.tags = (await res.json()).tags;
        btn.classList.toggle("on", p.tags.includes(t));
        btn.setAttribute("aria-pressed", String(p.tags.includes(t)));
        reindex(); renderDistricts(); renderPools(); renderWarnings(); applySearch();
      } }, t)));

    let mover = null;
    if (!ro()) {
      const sel = h("select", { "aria-label": "Move to" },
        h("option", { value: "" }, role === "minister" ? "— Not ministering —" : "— Not assigned —"),
        ...districts.flatMap(d => d.groups.map((g, gi) => {
          const names = g.ministers.map(m => person(m).display.split(" ")[0]).join(", ");
          return h("option", { value: g.key, selected: g.key === gkey },
            `${d.name} · Group ${gi + 1}${names ? ` (${names})` : ""}`);
        })));
      sel.addEventListener("change", () => { move(pid, role, gkey, sel.value || null); closePop(); });
      mover = h("label", { class: "stack-label" }, role === "minister" ? "Ministers in" : "Assigned to", sel);
    }

    showPop(el,
      h("h3", {}, p.display),
      facts ? h("p", { class: "muted small" }, facts) : null,
      mover,
      where.length ? h("p", { class: "small" }, where.join(" · ")) : null,
      h("div", { class: "small muted" }, "Tags"),
      tagBox,
      hist.length ? h("details", { class: "small" }, h("summary", {}, `History (${hist.length})`),
        h("ul", {}, ...hist.map(x => h("li", {}, x)))) : null);
  }

  function openGroupMenu(g, anchor) {
    const here = idx.group.get(g.key);
    const sel = h("select", { "aria-label": "District" },
      ...districts.map(d => h("option", { value: d.key, selected: d === here.d }, d.name)));
    sel.addEventListener("change", () => {
      const to = districts.find(d => d.key === sel.value);
      here.d.groups = here.d.groups.filter(x => x !== g);
      to.groups.push(g);
      closePop(); changed();
    });
    const count = g.ministers.length + g.assigned.length;
    showPop(anchor,
      h("h3", {}, groupLabel(g.key)),
      h("label", { class: "stack-label" }, "Move group to", sel),
      h("button", { type: "button", class: "link danger", onclick: () => {
        if (count && !confirm(`Delete this group? Its ${count} name(s) go back to the side lists.`)) return;
        here.d.groups = here.d.groups.filter(x => x !== g);
        closePop(); changed();
      } }, "Delete group"));
  }

  function deleteDistrict(d) {
    const n = d.groups.length;
    if (n && !confirm(`Delete ${d.name} and its ${n} group(s)? Everyone in them goes back to the side lists.`)) return;
    districts = districts.filter(x => x !== d);
    changed();
  }

  // --- drag and drop (mouse, pen, and touch via long-press) -------------------
  let drag = null;   // {el, pid, role, from, ghost, startX, startY, active, timer, pointerId, over}

  document.addEventListener("pointerdown", (e) => {
    const el = e.target.closest(".chip");
    if (!el || e.button > 0) return;
    drag = { el, pid: Number(el.dataset.pid), role: el.dataset.role, from: el.dataset.gkey || null,
             startX: e.clientX, startY: e.clientY, active: false, pointerId: e.pointerId, touch: e.pointerType === "touch" };
    if (drag.touch && !ro()) drag.timer = setTimeout(() => startDrag(e.clientX, e.clientY), 280);
  });

  document.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    const dist = Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY);
    if (!drag.active) {
      if (drag.touch) { if (dist > 8) { clearTimeout(drag.timer); drag = null; } return; }  // it's a scroll
      if (dist > 5 && !ro()) startDrag(e.clientX, e.clientY); else return;
    }
    moveGhost(e.clientX, e.clientY);
  });

  document.addEventListener("pointerup", (e) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    clearTimeout(drag.timer);
    const d = drag;
    drag = null;
    if (!d.active) { openPerson(d.el); return; }
    endDrag(d);
  });
  document.addEventListener("pointercancel", () => { if (drag) { clearTimeout(drag.timer); if (drag.active) endDrag(drag, true); drag = null; } });
  // Keyboard: Enter/Space on a name opens its menu (pointer clicks are handled above).
  document.addEventListener("click", (e) => {
    const el = e.target.closest(".chip");
    if (el && e.detail === 0) openPerson(el);
  });
  // While dragging by touch, stop the page from scrolling under the finger.
  document.addEventListener("touchmove", (e) => { if (drag && drag.active) e.preventDefault(); }, { passive: false });

  function startDrag(x, y) {
    closePop();
    drag.active = true;
    drag.ghost = drag.el.cloneNode(true);
    drag.ghost.classList.add("ghost");
    document.body.append(drag.ghost);
    drag.el.classList.add("dragging");
    document.body.classList.add("is-dragging", `drag-${drag.role}`);
    showTray(drag.role, true);  // so there's somewhere to drop it back
    if (navigator.vibrate && drag.touch) navigator.vibrate(15);
    moveGhost(x, y);
  }

  let scrollRaf = null;
  function moveGhost(x, y) {
    drag.ghost.style.transform = `translate(${x + 8}px, ${y + 8}px)`;
    moveGhostTarget(x, y);
    autoScroll(x, y);
  }

  function moveGhostTarget(x, y) {
    const target = document.elementFromPoint(x, y)?.closest(".zone");
    const ok = target && target.dataset.role === drag.role ? target : null;
    if (drag.over && drag.over !== ok) drag.over.classList.remove("over");
    if (ok) ok.classList.add("over");
    drag.over = ok;
  }

  // Scroll the districts area (not the page) when dragging near its edges. The tray above it
  // stays put, so a name can be carried from the tray to any group.
  function autoScroll(x, y) {
    cancelAnimationFrame(scrollRaf);
    if (!drag || !drag.active) return;
    const edge = 56, speed = 16;
    const r = elDistricts.getBoundingClientRect();
    if (y < r.top) return;  // over the tray or header
    const dy = y < r.top + edge ? -speed : y > r.bottom - edge ? speed : 0;
    const dx = x < r.left + edge ? -speed : x > r.right - edge ? speed : 0;
    if (!dx && !dy) return;
    scrollRaf = requestAnimationFrame(() => {
      elDistricts.scrollTop += dy;
      elDistricts.scrollLeft += dx;
      if (drag && drag.active) { autoScroll(x, y); moveGhostTarget(x, y); }
    });
  }

  function endDrag(d, cancelled = false) {
    cancelAnimationFrame(scrollRaf);
    d.ghost.remove();
    d.el.classList.remove("dragging");
    document.body.classList.remove("is-dragging", "drag-minister", "drag-assigned");
    if (d.over) d.over.classList.remove("over");
    if (!cancelled && d.over) move(d.pid, d.role, d.from, d.over.dataset.gkey || null);
  }

  // --- header controls --------------------------------------------------------
  const nameInput = $("#layout-name");
  nameInput.addEventListener("change", () => { if (S.layout.kind === "draft") patchMeta({ name: nameInput.value }); });
  nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") nameInput.blur(); });
  const statusSel = $("#status");
  if (statusSel) statusSel.addEventListener("change", async () => {
    if (dirty || saving) { clearTimeout(saveTimer); await save(); }
    patchMeta({ status: statusSel.value });
  });

  for (const sel of document.querySelectorAll("select.scope")) {
    const pool = sel.dataset.pool;
    sel.value = scopes[pool];
    sel.addEventListener("change", () => {
      scopes[pool] = sel.value;
      try { localStorage.setItem(`pool-scope-${S.org}-${pool}`, sel.value); } catch {}
      renderPools(); renderWarnings(); applySearch();
    });
  }

  // --- tray -----------------------------------------------------------------
  const tray = $("#tray"), trayToggle = $("#tray-toggle");
  function showTray(name, open = false) {
    for (const b of document.querySelectorAll("[data-tray-tab]")) b.setAttribute("aria-selected", String(b.dataset.trayTab === name));
    for (const el of document.querySelectorAll("[data-tray]")) el.hidden = el.dataset.tray !== name;
    if (open) setTrayOpen(true);
  }
  function setTrayOpen(open) {
    tray.classList.toggle("collapsed", !open);
    trayToggle.textContent = open ? "Hide" : "Show";
    trayToggle.setAttribute("aria-expanded", String(open));
  }
  for (const b of document.querySelectorAll("[data-tray-tab]")) b.addEventListener("click", () => showTray(b.dataset.trayTab, true));
  trayToggle.addEventListener("click", () => setTrayOpen(tray.classList.contains("collapsed")));

  const search = $("#search");
  search.addEventListener("input", () => {
    query = search.value.trim().toLowerCase();
    const first = applySearch();
    if (first) first.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
  });

  render();
  elSave.textContent = ro() ? "" : "All changes saved";
})();
