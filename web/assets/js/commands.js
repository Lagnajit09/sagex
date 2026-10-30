/* Commands page: renders window.SAGEX_COMMANDS (generated from the CLI by
   web/tools/build_commands.py) as a searchable, deep-linkable reference. */
(() => {
  "use strict";

  const data = window.SAGEX_COMMANDS;
  const root = document.querySelector("[data-commands]");
  if (!data || !root) return;

  const input = document.querySelector("[data-cmd-search]");
  const count = document.querySelector("[data-cmd-count]");
  const nav = document.querySelector("[data-cmd-groups]");

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const slug = (c) => `cmd-${c.group}${c.name === c.group ? "" : "-" + c.name}`;

  // Escape `text`, wrapping case-insensitive matches of `needle` in <mark>.
  function highlight(text, needle) {
    if (!needle) return esc(text);
    const lower = text.toLowerCase();
    let out = "";
    let i = 0;
    for (let at = lower.indexOf(needle); at !== -1; at = lower.indexOf(needle, i)) {
      out += esc(text.slice(i, at)) + "<mark>" + esc(text.slice(at, at + needle.length)) + "</mark>";
      i = at + needle.length;
    }
    return out + esc(text.slice(i));
  }

  // Help text → HTML: `backtick spans` and --flags become <code>; search matches are marked.
  function rich(text, needle) {
    return text.split("`").map((part, i) => {
      if (i % 2) return `<code>${highlight(part, needle)}</code>`;
      return part.split(/((?<![\w-])--?[a-z][a-z0-9-]*)/).map((piece, j) =>
        j % 2 ? `<code>${highlight(piece, needle)}</code>` : highlight(piece, needle)).join("");
    }).join("");
  }

  function usageHTML(usage) {
    return esc(usage)
      .replace(/^sagex/, '<span class="cmd-sagex">sagex</span>')
      .replace(/(&lt;[^&]+&gt;|\[[^\]]+\])/g, '<span class="cmd-arg">$1</span>');
  }

  const COPY_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>';
  const CHEVRON = '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';

  function paramRows(params) {
    return params.map((p) => {
      if (p.kind === "arg") {
        const name = p.required ? `&lt;${esc(p.name)}&gt;` : `[${esc(p.name)}]`;
        return `<tr><td>${name} <em>${p.required ? "required" : "optional"}</em></td><td>${rich(p.help, "")}</td></tr>`;
      }
      const flags = p.flags.map(esc).join(", ") + (p.takes_value ? " <em>&lt;value&gt;</em>" : "");
      return `<tr><td>${flags}</td><td>${rich(p.help, "")}</td></tr>`;
    }).join("");
  }

  function commandHTML(c, needle) {
    const rows = paramRows(c.params);
    return `<details class="cmd" id="${slug(c)}">
      <summary><span class="usage">${usageHTML(c.usage)}</span><span class="sum">${rich(c.summary, needle)}</span>${CHEVRON}</summary>
      <div class="cmd-body">
        ${c.details.map((d) => `<p>${rich(d, needle)}</p>`).join("")}
        ${rows ? `<table><tbody>${rows}</tbody></table>` : ""}
        <div class="copy-line" data-copyable><code>${esc(c.usage)}</code><button class="copy-btn" type="button">${COPY_ICON}<span class="lbl">Copy</span></button></div>
      </div>
    </details>`;
  }

  const haystack = (c) => [c.usage, c.summary, ...c.details, ...c.params.flatMap((p) => [p.name || "", ...(p.flags || []), p.help])]
    .join(" ").toLowerCase();

  function render() {
    const needle = (input && input.value.trim().toLowerCase()) || "";
    let shown = 0;
    const html = data.groups.map((g) => {
      const cmds = data.commands.filter((c) => c.group === g.name && (!needle || haystack(c).includes(needle)));
      shown += cmds.length;
      if (!cmds.length) return "";
      return `<section class="cmd-group" id="group-${g.name}">
        <header><h2>sagex ${esc(g.name)}</h2><p>${rich(g.blurb, "")}</p></header>
        ${cmds.map((c) => commandHTML(c, needle)).join("")}
      </section>`;
    }).join("");
    root.innerHTML = html || `<p class="cmd-empty">No commands match “${esc(needle)}”.</p>`;
    if (count) count.textContent = needle ? `${shown} of ${data.commands.length}` : `${data.commands.length} commands`;
  }

  if (nav) {
    nav.innerHTML = data.groups.map((g) => `<li><a href="#group-${g.name}">${esc(g.name)}</a></li>`).join("");
  }

  render();
  if (input) {
    input.addEventListener("input", render);
    document.addEventListener("keydown", (e) => {
      if (e.key === "/" && document.activeElement !== input && !/input|textarea/i.test(document.activeElement.tagName)) {
        e.preventDefault();
        input.focus();
      }
      if (e.key === "Escape" && document.activeElement === input) { input.value = ""; render(); }
    });
  }

  // Deep link: #cmd-run-workflow opens that command.
  function openFromHash() {
    const target = location.hash && document.getElementById(location.hash.slice(1));
    if (target && target.tagName === "DETAILS") {
      target.open = true;
      // Instant, not smooth: a smooth scroll is cut short when web fonts load and shift the layout.
      target.scrollIntoView({ block: "start", behavior: "instant" });
    }
  }
  window.addEventListener("hashchange", openFromHash);
  openFromHash();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(openFromHash);   // re-align after the shift
})();
