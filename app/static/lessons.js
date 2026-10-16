// Lesson planning board: every Sunday between two general conferences, each with the talks taught.
// Plain JS, no dependencies (same approach as board.js). Every change re-renders and autosaves.
(() => {
  "use strict";

  const S = JSON.parse(document.getElementById("state").textContent);
  const SOON_DAYS = 21;   // warn about Sundays this close with nothing planned
  const $ = (sel, root = document) => root.querySelector(sel);
  const elWeeks = $("#weeks");
  const elPop = $("#popover");
  const talks = new Map(S.talks.map((t, i) => [t.id, { ...t, order: i }]));
  const weeks = S.weeks.map(w => ({ ...w, talks: [...w.talks],
    teachers: Object.fromEntries(S.orgs.map(o => [o, [...(w.teachers[o] || [])]])) }));
  const teachers = new Map(S.orgs.flatMap(o => S.teachers[o].map(t => [t.id, t])));
  const SHORT = { eq: "EQ", rs: "RS" };
  const weekBy = new Map(weeks.map(w => [w.date, w]));
  const today = S.today;
  const nextDate = S.weeks.find(w => w.date >= today)?.date;
  let version = S.version;
  let query = "";

  const talk = (id) => talks.get(id) || { id, title: "Unknown talk", speaker: "", required: false, kind: "talk", order: 1e9 };
  const teacher = (id) => teachers.get(id) || { id, name: "Unknown", active: false, moved_out: false };
  const isLesson = (w) => w.status === "lesson";
  const hasTeachers = (w) => S.orgs.some(o => w.teachers[o].length);
  const lastName = (name) => name.split(" ").slice(-1)[0];

  // --- tiny DOM helper (always textContent, never innerHTML, for data) --------
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

  // --- dates ------------------------------------------------------------------
  const asDate = (iso) => new Date(`${iso}T12:00:00`);
  const daysUntil = (iso) => Math.round((asDate(iso) - asDate(today)) / 86400000);
  const label = (iso) => asDate(iso).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  function when(iso) {
    const n = daysUntil(iso);
    if (n < 0) return "Past";
    if (n === 0) return "Today";
    if (n < 14) return `In ${n} days`;
    return "";
  }

  // --- derived lookups --------------------------------------------------------
  let placed;     // talk id -> [dates]
  let teaching;   // teacher id -> [dates]
  function reindex() {
    placed = new Map();
    teaching = new Map();
    for (const w of weeks) {
      for (const t of w.talks) (placed.get(t) || placed.set(t, []).get(t)).push(w.date);
      for (const o of S.orgs) for (const t of w.teachers[o]) (teaching.get(t) || teaching.set(t, []).get(t)).push(w.date);
    }
  }

  function coverage() {
    const covered = new Set();
    for (const w of weeks) if (isLesson(w)) for (const id of w.talks) {
      const t = talk(id);
      if (t.required && t.kind === "talk") covered.add(t.speaker);
    }
    const missing = S.required_speakers.filter(s => !covered.has(s));
    return { covered, missing, total: S.required_speakers.length };
  }

  // --- mutations --------------------------------------------------------------
  function move(id, from, to) {
    if (from === to) return;
    if (from) { const w = weekBy.get(from); w.talks = w.talks.filter(t => t !== id); }
    if (to) { const w = weekBy.get(to); if (!w.talks.includes(id)) w.talks.push(id); }
    changed();
  }

  function moveTeacher(id, org, from, to) {
    if (from === to) return;
    if (from) { const w = weekBy.get(from); w.teachers[org] = w.teachers[org].filter(t => t !== id); }
    if (to) { const w = weekBy.get(to); if (!w.teachers[org].includes(id)) w.teachers[org].push(id); }
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
    const body = { version, weeks: weeks.map(w => ({ date: w.date, talks: w.talks, note: w.note, teachers: w.teachers })) };
    try {
      const res = await fetch(`/api/lessons/${S.plan}/${S.conference.id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (res.status === 409) {
        $("#conflict").hidden = false;
        elSave.textContent = "Not saved";
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

  // Sunday marks (stake conference etc.) are ward-wide and saved on their own, straight away.
  async function setDay(w, status, note = w.day_note) {
    elSave.textContent = "Saving…";
    try {
      const res = await fetch(`/api/lessons/days/${w.date}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status, note }) });
      if (!res.ok) throw new Error();
      const out = await res.json();
      w.status = out.status; w.day_note = out.note;
      elSave.textContent = dirty ? "Unsaved changes…" : "All changes saved";
    } catch {
      elSave.textContent = "Couldn't save. Check your connection.";
    }
    render();
  }

  // --- rendering --------------------------------------------------------------
  function chip(id, from) {
    const t = talk(id);
    const cls = ["tchip"];
    if (t.required) cls.push("req");
    if (t.kind === "business") cls.push("biz");
    if (!from && placed.has(id)) cls.push("used");
    const where = placed.has(id) ? `Planned for ${placed.get(id).map(label).join(", ")}` : "Not planned";
    return h("button", { type: "button", class: cls.join(" "), dataset: { tid: id, from: from || "" },
                         title: [t.title, t.speaker, t.calling, where].filter(Boolean).join(" · ") },
      t.required ? h("span", { class: "star", "aria-hidden": "true" }, "★") : null,
      h("span", { class: "ttl" }, t.title),
      h("span", { class: "spk" }, lastName(t.speaker)));
  }

  function teacherChip(id, org, from) {
    const t = teacher(id);
    const cls = ["pchip", `o-${org}`];
    if (t.moved_out) cls.push("moved");
    if (!t.active) cls.push("inactive");
    const n = (teaching.get(id) || []).length;
    return h("button", { type: "button", class: cls.join(" "), dataset: { teacher: id, org, from: from || "" },
                         title: [t.name, t.moved_out ? "Moved out" : null, !t.active ? "Off the teacher list" : null,
                                 `${n} lesson${n === 1 ? "" : "s"} this conference`].filter(Boolean).join(" · ") },
      h("span", { class: "nm" }, t.name),
      from ? null : h("span", { class: "cnt" }, String(n)));
  }

  const teacherLabel = (org) => (S.orgs.length > 1 ? `${SHORT[org]} teacher` : "Teacher");

  // Sundays before this one, back to the previous lesson Sunday (to spot back-to-back teaching).
  function previousLesson(w) {
    const i = weeks.indexOf(w);
    for (let j = i - 1; j >= 0; j--) if (isLesson(weeks[j])) return weeks[j];
    return null;
  }

  function weekWarnings(w) {
    const out = [];
    const n = daysUntil(w.date);
    if (!isLesson(w) && (w.talks.length || hasTeachers(w))) out.push(`Lesson planned on a ${S.statuses[w.status].toLowerCase()}`);
    if (!isLesson(w) || n < 0) return out;
    if (!w.talks.length && n <= SOON_DAYS) out.push("No talk yet");
    const prev = previousLesson(w);
    for (const o of S.orgs) {
      if (!w.teachers[o].length && n <= SOON_DAYS) out.push(`No ${teacherLabel(o).toLowerCase()} yet`);
      for (const id of w.teachers[o]) {
        const t = teacher(id);
        if (t.moved_out) out.push(`${t.name} moved out`);
        else if (!t.active) out.push(`${t.name} is off the teacher list`);
        if (prev && prev.teachers[o].includes(id)) out.push(`${t.name} also teaches the Sunday before`);
      }
    }
    return out;
  }

  function renderWeek(w) {
    const n = daysUntil(w.date);
    const warns = weekWarnings(w);
    const cls = ["week"];
    if (n < 0) cls.push("past");
    if (w.date === nextDate) cls.push("next");
    if (!isLesson(w)) cls.push("off");
    if (warns.length) cls.push("has-warn");
    const statusSel = h("select", { class: "w-status", "aria-label": `${label(w.date)}: lesson or not (whole ward)`,
                                    title: "Applies to the whole ward" },
      ...Object.entries(S.statuses)
        .filter(([k]) => k !== "fifth_sunday" || w.fifth)
        .map(([k, v]) => h("option", { value: k, selected: k === w.status }, v)));
    statusSel.addEventListener("change", () => setDay(w, statusSel.value, ""));
    const showTalks = isLesson(w) || w.talks.length;
    return h("article", { class: cls.join(" "), id: `w-${w.date}`, dataset: { date: w.date } },
      h("header", {},
        h("div", { class: "w-date" }, label(w.date), when(w.date) ? h("span", { class: "w-when" }, when(w.date)) : null),
        warns.length ? h("span", { class: "g-warn", title: warns.join("\n") }, `⚠ ${warns.length}`) : null,
        statusSel),
      isLesson(w) ? null : h("div", { class: "off-note" },
        h("input", { class: "w-daynote", value: w.day_note, maxlength: 120, placeholder: `${S.statuses[w.status]} (note for the whole ward)`,
          "aria-label": "Note for the whole ward", onchange: (e) => setDay(w, w.status, e.target.value) })),
      showTalks ? h("div", { class: "zone talks", dataset: { zone: "week", date: w.date } },
        ...w.talks.map(id => chip(id, w.date)),
        w.talks.length ? null : h("div", { class: "zone-empty" }, "Drop a talk here")) : null,
      ...S.orgs.filter(o => isLesson(w) || w.teachers[o].length).map(o =>
        h("div", { class: `zone teach o-${o}`, dataset: { zone: "teach", org: o, date: w.date } },
          h("div", { class: "zone-label" }, teacherLabel(o)),
          ...w.teachers[o].map(id => teacherChip(id, o, w.date)),
          w.teachers[o].length ? null : h("div", { class: "zone-empty" }, "Drop a teacher here"))),
      isLesson(w) ? h("input", { class: "w-note", value: w.note, maxlength: 200, placeholder: "Note", "aria-label": "Note",
        onchange: (e) => { w.note = e.target.value.trim(); changed(); } }) : null);
  }

  function renderWeeks() {
    const { scrollTop } = elWeeks;
    elWeeks.replaceChildren(...weeks.map(renderWeek));
    elWeeks.scrollTop = scrollTop;
  }

  const byOrder = (a, b) => talk(a).order - talk(b).order;
  function renderPools() {
    const all = [...talks.keys()].sort(byOrder);
    const open = all.filter(id => !placed.has(id) && talk(id).kind === "talk")
      .sort((a, b) => (talk(b).required - talk(a).required) || byOrder(a, b));
    $("#pool-open").replaceChildren(...open.map(id => chip(id, null)),
      open.length ? "" : h("div", { class: "zone-empty" }, "Every talk is planned 🎉"));
    $("#pool-all").replaceChildren(...all.map(id => chip(id, null)));
    $("#n-open").textContent = open.length;
    $("#n-all").textContent = all.length;

    const pool = $("#pool-teachers");
    pool.replaceChildren(...S.orgs.map(o => {
      const list = [...teachers.values()].filter(t => t.org === o && t.active)
        .sort((a, b) => a.name.localeCompare(b.name));
      return h("div", { class: "tgroup" },
        S.orgs.length > 1 ? h("div", { class: "zone-label" }, `${S.org_names[o]} teachers`) : null,
        h("div", { class: "zone tpool", dataset: { zone: "tpool", org: o } },
          ...list.map(t => teacherChip(t.id, o, null)),
          h("button", { type: "button", class: "add-teacher", onclick: (e) => openAddTeacher(o, e.currentTarget) }, "+ Add teacher")));
    }));
    $("#n-teachers").textContent = [...teachers.values()].filter(t => t.active).length;
  }

  function computeWarnings() {
    const list = [];
    const { missing, total } = coverage();
    const openAhead = weeks.filter(w => isLesson(w) && !w.talks.length && daysUntil(w.date) >= 0);
    if (missing.length) {
      list.push({ text: `${missing.length} of ${total} First Presidency and Twelve speakers aren't planned yet: ${missing.join(", ")}`, tray: "open" });
      if (missing.length > openAhead.length) {
        const n = missing.length - openAhead.length;
        list.push({ text: `Only ${openAhead.length} open Sunday${openAhead.length === 1 ? "" : "s"} left before ${S.conference.next_name} conference: combine at least ${n} lesson${n === 1 ? "" : "s"}`, tray: "open" });
      }
    } else if (total) {
      list.push({ text: `All ${total} First Presidency and Twelve speakers are planned`, info: true });
    }
    for (const w of weeks) for (const text of weekWarnings(w)) list.push({ text: `${label(w.date)}: ${text}`, date: w.date });
    for (const [id, dates] of placed) {
      const lessonDates = dates.filter(d => isLesson(weekBy.get(d)));
      if (lessonDates.length > 1) list.push({ text: `“${talk(id).title}” is planned ${lessonDates.length} times`, date: lessonDates[1] });
      if (talk(id).kind === "business") list.push({ text: `“${talk(id).title}” isn't a lesson talk`, date: dates[0], info: true });
    }
    for (const w of weeks) if (w.talks.length > 2) list.push({ text: `${label(w.date)}: ${w.talks.length} talks combined`, date: w.date, info: true });
    for (const o of S.orgs) {
      const counts = [...teachers.values()].filter(t => t.org === o && t.active)
        .map(t => [t.name, (teaching.get(t.id) || []).length]);
      if (counts.length > 1) {
        const nums = counts.map(c => c[1]), max = Math.max(...nums), min = Math.min(...nums);
        if (max - min >= 3) list.push({ text: `${S.org_names[o]} teachers are uneven: ${counts.sort((a, b) => b[1] - a[1]).map(c => `${c[0]} ${c[1]}`).join(", ")}`, tray: "teachers", info: true });
      }
    }
    return list;
  }

  function renderWarnings() {
    const list = computeWarnings();
    $("#warn-count").textContent = list.filter(w => !w.info).length || "";
    $("#warnings").replaceChildren(...(list.length ? list.map(w => h("li", { class: w.info ? "info" : "" },
      h("button", { type: "button", class: "link", onclick: () => focusWarning(w) }, w.text)))
      : [h("li", { class: "muted" }, "No warnings")]));
    const { missing, total } = coverage();
    $("#coverage").textContent = total ? `★ ${total - missing.length}/${total} planned` : "";
    $("#coverage").classList.toggle("done", total > 0 && !missing.length);
  }

  function focusWarning(w) {
    if (w.date) flashEl(document.getElementById(`w-${w.date}`));
    else if (w.tray) showTray(w.tray, true);
  }
  function flashEl(el) {
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.remove("flash-hl"); void el.offsetWidth; el.classList.add("flash-hl");
  }

  function applySearch() {
    document.body.classList.toggle("searching", !!query);  // dims everything that doesn't match
    let first = null;
    for (const el of document.querySelectorAll(".tchip, .pchip")) {
      const text = el.dataset.teacher ? teacher(Number(el.dataset.teacher)).name
        : `${talk(Number(el.dataset.tid)).title} ${talk(Number(el.dataset.tid)).speaker}`;
      const hit = !!query && text.toLowerCase().includes(query);
      el.classList.toggle("hit", hit);
      if (hit && !first) first = el;
    }
    return first;
  }

  function render() {
    reindex();
    renderWeeks();
    renderPools();
    renderWarnings();
    applySearch();
  }

  // --- popover ----------------------------------------------------------------
  let popAnchor = null;
  function showPop(anchor, ...content) {
    popAnchor = anchor;
    elPop.replaceChildren(h("button", { type: "button", class: "icon close", "aria-label": "Close", onclick: closePop }, "✕"),
      ...content.filter(c => c != null && c !== false));
    elPop.hidden = false;
    const r = anchor.getBoundingClientRect();
    if (window.innerWidth < 640) { elPop.style.left = elPop.style.top = ""; return; }  // CSS bottom sheet
    const w = elPop.offsetWidth, ht = elPop.offsetHeight;
    const left = Math.min(r.left, window.innerWidth - w - 12);
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

  function weekOption(w, selected) {
    const names = w.talks.map(id => lastName(talk(id).speaker)).join(", ");
    return h("option", { value: w.date, selected }, `${label(w.date)}${names ? ` (${names})` : ""}`);
  }

  function openTalk(el) {
    const id = Number(el.dataset.tid), from = el.dataset.from || null;
    const t = talk(id);
    const targets = weeks.filter(w => isLesson(w) || w.date === from);
    const sel = h("select", { "aria-label": from ? "Taught on" : "Add to" },
      h("option", { value: "" }, from ? "— Not planned —" : "— Pick a Sunday —"),
      ...targets.map(w => weekOption(w, w.date === from)));
    sel.addEventListener("change", () => { move(id, from, sel.value || null); closePop(); });
    const dates = placed.get(id) || [];
    showPop(el,
      h("h3", {}, t.title),
      h("p", { class: "small" }, h("strong", {}, t.speaker), t.calling ? ` · ${t.calling}` : ""),
      t.required ? h("p", { class: "small req-note" }, "★ First Presidency or Twelve: needs a lesson") : null,
      t.kind === "business" ? h("p", { class: "small muted" }, "Not a lesson talk (sustaining or report)") : null,
      t.summary ? h("p", { class: "small muted" }, t.summary) : null,
      h("a", { href: t.url, target: "_blank", rel: "noopener", class: "small" }, "Read the talk ↗"),
      h("label", { class: "stack-label" }, from ? "Taught on" : "Add to", sel),
      !from && dates.length ? h("p", { class: "small muted" }, `Already planned for ${dates.map(label).join(", ")}`) : null);
  }

  function openTeacher(el) {
    const id = Number(el.dataset.teacher), org = el.dataset.org, from = el.dataset.from || null;
    const t = teacher(id);
    const dates = teaching.get(id) || [];
    const targets = weeks.filter(w => isLesson(w) || w.date === from);
    const sel = h("select", { "aria-label": from ? "Teaches on" : "Assign to" },
      h("option", { value: "" }, from ? "— Not teaching —" : "— Pick a Sunday —"),
      ...targets.map(w => {
        const who = w.teachers[org].map(x => teacher(x).name.split(" ")[0]).join(", ");
        return h("option", { value: w.date, selected: w.date === from }, `${label(w.date)}${who ? ` (${who})` : ""}`);
      }));
    sel.addEventListener("change", () => { moveTeacher(id, org, from, sel.value || null); closePop(); });
    showPop(el,
      h("h3", {}, t.name),
      h("p", { class: "small muted" }, [S.org_names[org], t.moved_out ? "Moved out" : null,
                                        !t.active ? "Off the teacher list" : null].filter(Boolean).join(" · ")),
      h("label", { class: "stack-label" }, from ? "Teaches on" : "Assign to", sel),
      h("p", { class: "small" }, dates.length ? `Teaching ${dates.map(label).join(", ")}` : "No lessons yet this conference"),
      h("a", { href: "/lessons/teachers", class: "small" }, "Manage teachers"));
  }

  function openAddTeacher(org, anchor) {
    const taken = new Set([...teachers.values()].filter(t => t.org === org && t.person_id).map(t => t.person_id));
    const people = S.roster[org].filter(p => !taken.has(p.id));
    const pick = people.length ? h("select", { "aria-label": "From the roster" },
      h("option", { value: "" }, "— Pick someone —"), ...people.map(p => h("option", { value: p.id }, p.name))) : null;
    const typed = h("input", { maxlength: 80, placeholder: people.length ? "Or type a name" : "Name", "aria-label": "Name" });
    const err = h("p", { class: "small error-text", hidden: true });
    const add = h("button", { type: "button", onclick: async () => {
      const body = { org, person_id: pick && pick.value ? Number(pick.value) : null, name: typed.value };
      const res = await fetch("/api/lessons/teachers", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const out = await res.json().catch(() => ({}));
      if (!res.ok) { err.textContent = out.error || "Couldn't add the teacher."; err.hidden = false; return; }
      teachers.set(out.id, out);
      closePop();
      render();
    } }, "Add teacher");
    showPop(anchor,
      h("h3", {}, `New ${S.org_names[org]} teacher`),
      pick ? h("label", { class: "stack-label" }, "From the roster", pick) : null,
      h("label", { class: "stack-label" }, pick ? "Or type a name" : "Name", typed),
      err, add);
  }

  // --- fill in First Presidency and Twelve ------------------------------------
  function autofill() {
    const { missing } = coverage();
    if (!missing.length) { alert("Every First Presidency and Twelve speaker is already planned."); return; }
    const picks = missing.map(speaker => {
      const theirs = [...talks.values()].filter(t => t.speaker === speaker && t.kind === "talk" && t.required)
        .sort((a, b) => a.order - b.order);
      return (theirs.find(t => !/^closing remarks$/i.test(t.title)) || theirs[0]).id;
    });
    const open = weeks.filter(w => isLesson(w) && !w.talks.length && daysUntil(w.date) >= 0);
    const combine = Math.max(0, picks.length - open.length);
    if (!confirm(`Put ${picks.length} talk${picks.length === 1 ? "" : "s"} on the open Sundays, in conference order?`
                 + (combine ? ` There aren't enough open Sundays, so ${combine} will share a Sunday with another talk.` : ""))) return;
    picks.forEach((id, i) => {
      if (i < open.length) open[i].talks.push(id);
      else {   // spread the rest over the future lesson Sundays with the fewest talks
        const ahead = weeks.filter(w => isLesson(w) && daysUntil(w.date) >= 0);
        ahead.sort((a, b) => a.talks.length - b.talks.length || a.date.localeCompare(b.date))[0]?.talks.push(id);
      }
    });
    changed();
  }
  $("#autofill").addEventListener("click", autofill);

  // --- drag and drop (mouse, pen, and touch via long-press) -------------------
  let drag = null;

  document.addEventListener("pointerdown", (e) => {
    const el = e.target.closest(".tchip, .pchip");
    if (!el || e.button > 0) return;
    const isTeacher = !!el.dataset.teacher;
    drag = { el, kind: isTeacher ? "teacher" : "talk", id: Number(isTeacher ? el.dataset.teacher : el.dataset.tid),
             org: el.dataset.org || null, from: el.dataset.from || null,
             startX: e.clientX, startY: e.clientY, active: false, pointerId: e.pointerId, touch: e.pointerType === "touch" };
    if (drag.touch) drag.timer = setTimeout(() => startDrag(e.clientX, e.clientY), 280);
  });
  document.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    const dist = Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY);
    if (!drag.active) {
      if (drag.touch) { if (dist > 8) { clearTimeout(drag.timer); drag = null; } return; }  // it's a scroll
      if (dist > 5) startDrag(e.clientX, e.clientY); else return;
    }
    moveGhost(e.clientX, e.clientY);
  });
  document.addEventListener("pointerup", (e) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    clearTimeout(drag.timer);
    const d = drag;
    drag = null;
    if (!d.active) { d.kind === "teacher" ? openTeacher(d.el) : openTalk(d.el); return; }
    endDrag(d);
  });
  document.addEventListener("pointercancel", () => { if (drag) { clearTimeout(drag.timer); if (drag.active) endDrag(drag, true); drag = null; } });
  document.addEventListener("click", (e) => {   // keyboard: Enter/Space on a talk opens its menu
    const el = e.target.closest(".tchip, .pchip");
    if (el && e.detail === 0) (el.dataset.teacher ? openTeacher(el) : openTalk(el));
  });
  document.addEventListener("touchmove", (e) => { if (drag && drag.active) e.preventDefault(); }, { passive: false });

  function startDrag(x, y) {
    closePop();
    drag.active = true;
    drag.ghost = drag.el.cloneNode(true);
    drag.ghost.classList.add("ghost");
    document.body.append(drag.ghost);
    drag.el.classList.add("dragging");
    document.body.classList.add("is-dragging");
    if (drag.from) showTray(drag.kind === "teacher" ? "teachers" : "open", true);  // somewhere to drop it back
    document.body.classList.add(drag.kind === "teacher" ? `drag-teacher-${drag.org}` : "drag-talk");
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
    const zone = document.elementFromPoint(x, y)?.closest(".zone, .week");
    let target;
    if (drag.kind === "teacher") {   // only this organization's teacher lanes and list
      const fit = (z) => z && (z.dataset.zone === "teach" || z.dataset.zone === "tpool") && z.dataset.org === drag.org;
      target = zone?.classList.contains("week") ? zone.querySelector(`.zone.teach[data-org="${drag.org}"]`)
        : fit(zone) ? zone : null;
    } else {   // anywhere on a Sunday's card counts as its talk zone
      target = zone?.classList.contains("week") ? zone.querySelector(".zone.talks")
        : zone && (zone.dataset.zone === "week" || zone.dataset.zone === "pool") ? zone : null;
    }
    if (drag.over && drag.over !== target) drag.over.classList.remove("over");
    if (target) target.classList.add("over");
    drag.over = target || null;
  }
  function autoScroll(x, y) {
    cancelAnimationFrame(scrollRaf);
    if (!drag || !drag.active) return;
    const edge = 56, speed = 16;
    const r = elWeeks.getBoundingClientRect();
    if (y < r.top) return;
    const dy = y < r.top + edge ? -speed : y > r.bottom - edge ? speed : 0;
    if (!dy) return;
    scrollRaf = requestAnimationFrame(() => {
      elWeeks.scrollTop += dy;
      if (drag && drag.active) { autoScroll(x, y); moveGhostTarget(x, y); }
    });
  }
  function endDrag(d, cancelled = false) {
    cancelAnimationFrame(scrollRaf);
    d.ghost.remove();
    d.el.classList.remove("dragging");
    document.body.classList.remove("is-dragging", "drag-talk", "drag-teacher-eq", "drag-teacher-rs");
    if (d.over) d.over.classList.remove("over");
    if (cancelled || !d.over) return;
    const zone = d.over.dataset.zone;
    if (zone === "pool") move(d.id, d.from, null);
    else if (zone === "week") move(d.id, d.from, d.over.dataset.date);
    else if (zone === "tpool") moveTeacher(d.id, d.org, d.from, null);
    else if (zone === "teach") moveTeacher(d.id, d.org, d.from, d.over.dataset.date);
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
    if (first) first.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  render();
  elSave.textContent = "All changes saved";
  // Start at the next Sunday rather than at the beginning of the conference's lessons.
  if (nextDate) elWeeks.scrollTop = document.getElementById(`w-${nextDate}`).offsetTop - 8;  // .weeks is position: relative
})();
