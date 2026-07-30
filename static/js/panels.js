// Renders the left data pane (project stats + three-state inventory) and
// the right detail drawer (mock block / real read / dataset node detail).
const Panels = (() => {
  const STATE_LABEL = { ready: "Ready", mock: "Mock / to-build", referenced_missing: "Referenced-missing" };

  function badge(state) {
    return `<span class="badge badge--${state}"><span class="dot"></span>${STATE_LABEL[state] || state}</span>`;
  }

  function renderDataPane(host, { discovery, activeWebapp, onSectionClick, onBuiltUnusedClick }) {
    const c = discovery.counts;
    host.innerHTML = `
      <div class="data-pane__section">
        <h3>Project</h3>
        <div class="stat-grid">
          <div class="stat-tile"><div class="stat-tile__value">${c.datasets}</div><div class="stat-tile__label">Datasets</div></div>
          <div class="stat-tile"><div class="stat-tile__value">${c.recipes}</div><div class="stat-tile__label">Recipes</div></div>
          <div class="stat-tile"><div class="stat-tile__value">${c.zones}</div><div class="stat-tile__label">Flow zones</div></div>
          <div class="stat-tile"><div class="stat-tile__value">${c.webapps}</div><div class="stat-tile__label">Webapps</div></div>
        </div>
      </div>
      <div class="data-pane__section" id="section-inventory">
        <h3>${activeWebapp ? activeWebapp.name : "Webapp"} &middot; data situation</h3>
        <div id="inventory-groups"></div>
      </div>
      <div class="data-pane__section">
        <h3>Built-unused (${c.built_unused})</h3>
        <div id="built-unused-list"></div>
      </div>
    `;

    if (activeWebapp) {
      renderInventoryGroups(host.querySelector("#inventory-groups"), activeWebapp.sections, onSectionClick);
    }
  }

  function renderInventoryGroups(host, sections, onSectionClick) {
    const groups = { ready: [], mock: [], referenced_missing: [] };
    for (const s of sections) groups[s.state]?.push(s);

    host.innerHTML = Object.entries(groups)
      .map(([state, items]) => {
        if (!items.length) return "";
        const rows = items
          .map(
            (s) => `<div class="tree-row" data-section-id="${s.id}">
              <span class="tree-row__label">${escapeHtml(s.label)}</span>
              ${badge(state)}
            </div>`
          )
          .join("");
        return `<div class="tree-group">
          <div class="tree-group__label"><span>${STATE_LABEL[state]}</span><span>${items.length}</span></div>
          ${rows}
        </div>`;
      })
      .join("");

    host.querySelectorAll(".tree-row").forEach((row) => {
      row.addEventListener("click", () => {
        host.querySelectorAll(".tree-row").forEach((r) => r.classList.remove("is-selected"));
        row.classList.add("is-selected");
        const section = sections.find((s) => s.id === row.dataset.sectionId);
        onSectionClick(section);
      });
    });
  }

  function renderBuiltUnused(host, built_unused, onClick) {
    const shown = built_unused.slice(0, 25);
    host.innerHTML = shown
      .map((d) => `<div class="tree-row" data-name="${escapeHtml(d.name)}"><span class="tree-row__label">${escapeHtml(d.name)}</span></div>`)
      .join("") + (built_unused.length > shown.length ? `<div class="tree-row" style="color:var(--text-muted)">+${built_unused.length - shown.length} more</div>` : "");
    host.querySelectorAll(".tree-row[data-name]").forEach((row) => {
      row.addEventListener("click", () => onClick(row.dataset.name));
    });
  }

  function renderWorksheetTabs(host, webapps, activeId, onSelect) {
    host.innerHTML = webapps
      .map((w) => {
        const dupe = w.duplicate_of && w.duplicate_of.length ? `<span class="ws-tab__dupe" title="Identical backend.py to: ${w.duplicate_of.join(', ')}">&#9888;</span>` : "";
        return `<div class="ws-tab ${w.id === activeId ? "is-active" : ""}" data-id="${w.id}">${escapeHtml(w.name)} ${dupe}</div>`;
      })
      .join("");
    host.querySelectorAll(".ws-tab").forEach((tab) => {
      tab.addEventListener("click", () => onSelect(tab.dataset.id));
    });
  }

  function renderMockDetail(host, section, { onRunDerivability }) {
    const mb = section.mock_block;
    host.innerHTML = `
      <div class="drawer__header">
        <div><h3>${escapeHtml(mb.title || section.label)}</h3>
        <div style="margin-top:4px">${badge("mock")}</div></div>
      </div>
      <div class="drawer__body">
        ${mb.trigger_keywords.length ? `<p><strong>Trigger keywords:</strong> ${mb.trigger_keywords.map(escapeHtml).join(", ")}</p>` : ""}
        ${mb.mock_functions.length ? `<p><strong>Mock functions:</strong> ${mb.mock_functions.map(escapeHtml).join(", ")}</p>` : ""}
        ${mb.migration_hint ? `<p><strong>Migration hint:</strong> ${escapeHtml(mb.migration_hint)}${mb.migration_hint_dataset ? ` &rarr; <code>${escapeHtml(mb.migration_hint_dataset)}</code>` : ""}</p>` : ""}
        <p><strong>Lines:</strong> ${mb.start_line}&ndash;${mb.end_line}</p>
        <div class="snippet">${escapeHtml(mb.snippet)}</div>
        <button class="btn btn--primary" id="btn-derivability" style="margin-top:12px">Run LLM derivability analysis</button>
        <div id="derivability-result" style="margin-top:12px"></div>
      </div>
    `;
    host.querySelector("#btn-derivability").addEventListener("click", async (e) => {
      e.target.disabled = true;
      e.target.textContent = "Analyzing…";
      await onRunDerivability(host.querySelector("#derivability-result"));
      e.target.textContent = "Re-run analysis";
      e.target.disabled = false;
    });
  }

  function renderDerivabilityResult(host, result) {
    const engineNote = result.engine === "stub"
      ? `<p style="color:var(--text-muted);font-size:11px">${escapeHtml(result.overall_note)}</p>`
      : result.engine === "error"
      ? `<p style="color:var(--status-critical);font-size:11px">${escapeHtml(result.overall_note)}</p>`
      : `<p style="font-size:11px">${escapeHtml(result.overall_note)}</p>`;

    if (!result.fields.length) {
      host.innerHTML = engineNote + `<p style="color:var(--text-muted)">No candidate fields identified.</p>`;
      return;
    }
    host.innerHTML = engineNote + result.fields
      .map((f) => {
        const derivIcon = f.derivable ? "&#10003;" : f.derivable === false ? "&#10007;" : "?";
        const derivColor = f.derivable ? "var(--status-good)" : f.derivable === false ? "var(--status-critical)" : "var(--text-muted)";
        return `<div class="field-row">
          <div class="field-row__name"><span style="color:${derivColor}">${derivIcon}</span> ${escapeHtml(f.field)}</div>
          ${f.source_dataset ? `<div class="field-row__source">from <code>${escapeHtml(f.source_dataset)}</code> (${f.source_columns.map(escapeHtml).join(", ")})</div>` : ""}
          <div class="field-row__note">${escapeHtml(f.note)}</div>
        </div>`;
      })
      .join("");
  }

  function renderReadDetail(host, section) {
    const state = section.state;
    host.innerHTML = `
      <div class="drawer__header">
        <div><h3>${escapeHtml(section.label)}</h3>
        <div style="margin-top:4px">${badge(state)}</div></div>
      </div>
      <div class="drawer__body">
        ${section.matched_dataset ? `<p><strong>Dataset:</strong> ${escapeHtml(section.matched_dataset)}</p>` : `<p style="color:var(--status-critical)">No dataset named <code>${escapeHtml(section.label)}</code> found in this project.</p>`}
        ${section.required_columns.length ? `<p><strong>Required columns:</strong> ${section.required_columns.map(escapeHtml).join(", ")}</p>` : ""}
        ${section.missing_columns.length ? `<p style="color:var(--status-critical)"><strong>Missing:</strong> ${section.missing_columns.map(escapeHtml).join(", ")}</p>` : ""}
        ${section.real_read ? `<p style="color:var(--text-muted);font-size:11px">Read at line ${section.real_read.line_no}${section.real_read.resolved ? " (via resolved variable)" : ""}</p>` : ""}
      </div>
    `;
  }

  function renderNodeDetail(host, node, { readers, upstream }) {
    host.innerHTML = `
      <div class="drawer__header"><h3>${escapeHtml(node.id)}</h3></div>
      <div class="drawer__body">
        <p><strong>Type:</strong> ${escapeHtml(node.type)} ${node.terminal ? " &middot; terminal (delivery surface)" : ""}</p>
        <p><strong>Zone:</strong> ${escapeHtml(node.zone_name || "(none)")}</p>
        <p><strong>In / Out degree:</strong> ${node.in_degree} / ${node.out_degree}</p>
        ${readers.length ? `<p><strong>Read by:</strong> ${readers.map(escapeHtml).join(", ")}</p>` : `<p style="color:var(--text-muted)">Not read by any webapp (built-unused).</p>`}
        ${upstream.length ? `<p><strong>Upstream lineage:</strong> ${upstream.slice(0, 12).map(escapeHtml).join(" &larr; ")}</p>` : ""}
        <p><strong>Columns (${node.column_count}):</strong></p>
        <div class="snippet mono" id="node-columns-list">Loading columns&hellip;</div>
      </div>
    `;
  }

  function renderNodeColumns(host, columns) {
    const target = host.querySelector("#node-columns-list");
    if (!target) return;
    if (!columns.length) {
      target.textContent = "(no columns declared)";
      return;
    }
    target.textContent = columns.map((c) => `${c.name}  :  ${c.type}`).join("\n");
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  return {
    renderDataPane,
    renderBuiltUnused,
    renderWorksheetTabs,
    renderMockDetail,
    renderDerivabilityResult,
    renderReadDetail,
    renderNodeDetail,
    renderNodeColumns,
  };
})();
