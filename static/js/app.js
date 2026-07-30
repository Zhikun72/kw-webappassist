// Main controller: upload flow, theme toggle, wiring between the graph,
// data pane, worksheet tabs, and detail drawer.
(() => {
  const state = {
    analysisId: null,
    discovery: null,
    graphData: null,
    webappCache: new Map(), // id -> {webapp, sections}
    activeWebappId: null,
  };

  const el = {
    app: document.getElementById("app"),
    overlay: document.getElementById("upload-overlay"),
    dropzone: document.getElementById("dropzone"),
    fileInput: document.getElementById("file-input"),
    chooseBtn: document.getElementById("btn-choose-file"),
    progress: document.getElementById("upload-progress"),
    error: document.getElementById("upload-error"),
    toolbarMeta: document.getElementById("toolbar-meta"),
    dataPane: document.getElementById("data-pane"),
    tabs: document.getElementById("worksheet-tabs"),
    drawer: document.getElementById("drawer"),
    appBody: document.getElementById("app-body"),
    themeBtn: document.getElementById("btn-theme"),
    newUploadBtn: document.getElementById("btn-new-upload"),
  };

  // ---- Theme ----
  function applyStoredTheme() {
    const saved = localStorage.getItem("theme");
    if (saved) document.documentElement.dataset.theme = saved;
  }
  el.themeBtn.addEventListener("click", () => {
    const current = document.documentElement.dataset.theme;
    const next = current === "dark" ? "light" : current === "light" ? "" : "dark";
    if (next) {
      document.documentElement.dataset.theme = next;
      localStorage.setItem("theme", next);
    } else {
      delete document.documentElement.dataset.theme;
      localStorage.removeItem("theme");
    }
  });
  applyStoredTheme();

  // ---- Upload flow ----
  el.chooseBtn.addEventListener("click", () => el.fileInput.click());
  el.fileInput.addEventListener("change", () => {
    if (el.fileInput.files[0]) handleUpload(el.fileInput.files[0]);
  });
  ["dragenter", "dragover"].forEach((evt) =>
    el.dropzone.addEventListener(evt, (e) => { e.preventDefault(); el.dropzone.classList.add("is-dragover"); })
  );
  ["dragleave", "drop"].forEach((evt) =>
    el.dropzone.addEventListener(evt, (e) => { e.preventDefault(); el.dropzone.classList.remove("is-dragover"); })
  );
  el.dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) handleUpload(file);
  });
  el.newUploadBtn.addEventListener("click", () => {
    el.app.hidden = true;
    el.overlay.hidden = false;
    state.analysisId = null;
    state.webappCache.clear();
  });

  async function handleUpload(file) {
    el.error.hidden = true;
    el.progress.hidden = false;
    try {
      const result = await Api.upload(file);
      state.analysisId = result.analysis_id;
      state.discovery = result;
      await bootstrapAnalysis();
      el.overlay.hidden = true;
      el.app.hidden = false;
    } catch (err) {
      el.error.textContent = err.message;
      el.error.hidden = false;
    } finally {
      el.progress.hidden = true;
    }
  }

  // ---- Bootstrap after a successful upload ----
  async function bootstrapAnalysis() {
    renderToolbarMeta();

    state.graphData = await Api.graph(state.analysisId);
    Graph.init(document.getElementById("graph-svg"), { onNodeClick: onGraphNodeClick });
    Graph.render(state.graphData);

    await Promise.all(
      state.discovery.webapps.map(async (w) => {
        const detail = await Api.webapp(state.analysisId, w.id);
        state.webappCache.set(w.id, detail);
      })
    );

    state.activeWebappId = state.discovery.webapps[0]?.id || null;
    renderTabs();
    renderDataPane();
  }

  function renderToolbarMeta() {
    const m = state.discovery.manifest;
    el.toolbarMeta.textContent = `DSS ${m.generated_with_dss_version || "?"} · ${
      m.has_row_data ? "row data present" : "schema-only export"
    } · ${state.discovery.counts.datasets} datasets · ${state.discovery.counts.recipes} recipes`;
  }

  function renderTabs() {
    Panels.renderWorksheetTabs(el.tabs, state.discovery.webapps, state.activeWebappId, (id) => {
      state.activeWebappId = id;
      renderTabs();
      renderDataPane();
      clearDrawer();
    });
  }

  function renderDataPane() {
    const active = state.webappCache.get(state.activeWebappId);
    Panels.renderDataPane(el.dataPane, {
      discovery: state.discovery,
      activeWebapp: active ? { name: active.webapp.name, sections: active.sections } : null,
      onSectionClick: onSectionClick,
    });

    Panels.renderColumnsSummary(el.dataPane, active ? active.columnsSummary : null);
    if (active && !active.columnsSummary) {
      Api.webappColumns(state.analysisId, state.activeWebappId).then((res) => {
        active.columnsSummary = res.summary;
        // Only repaint if this webapp is still the one being viewed.
        if (state.activeWebappId === active.webapp.id) {
          Panels.renderColumnsSummary(el.dataPane, res.summary);
        }
      });
    }

    Panels.renderBuiltUnused(
      document.getElementById("built-unused-list"),
      // built_unused list comes from the inventory endpoint's shape; discovery already carries the count,
      // fetch lazily once and cache on state.discovery for reuse.
      state.discovery._built_unused || [],
      (name) => {
        const node = state.graphData.nodes.find((n) => n.id === name);
        if (node) onGraphNodeClick(node);
      }
    );
    if (!state.discovery._built_unused) {
      Api.inventory(state.analysisId).then((inv) => {
        state.discovery._built_unused = inv.built_unused;
        Panels.renderBuiltUnused(document.getElementById("built-unused-list"), inv.built_unused, (name) => {
          const node = state.graphData.nodes.find((n) => n.id === name);
          if (node) onGraphNodeClick(node);
        });
      });
    }
  }

  function openDrawer() {
    el.appBody.classList.add("drawer-open");
  }
  function clearDrawer() {
    el.appBody.classList.remove("drawer-open");
    el.drawer.innerHTML = `<div class="drawer__empty">Select a webapp section or a flow node to see detail here.</div>`;
  }

  function onSectionClick(section) {
    openDrawer();
    if (section.state === "mock") {
      const hintDataset = section.mock_block.migration_hint_dataset;
      const sourceExists = hintDataset
        ? state.graphData.nodes.some((n) => n.id === hintDataset && n.known_dataset)
        : false;
      Panels.renderMockGapCard(el.drawer, section, {
        sourceExists,
        onRunDerivability: async (resultHost) => {
          try {
            const result = await Api.derivability(state.analysisId, state.activeWebappId, section.id);
            Panels.renderDerivabilityResult(resultHost, result);
          } catch (err) {
            resultHost.innerHTML = `<p style="color:var(--status-critical)">${err.message}</p>`;
          }
        },
      });
      if (sourceExists) Graph.focusNode(hintDataset);
    } else {
      Panels.renderReadDetail(el.drawer, section);
      if (section.matched_dataset) Graph.focusNode(section.matched_dataset);
    }
  }

  function onGraphNodeClick(node) {
    openDrawer();
    const readers = [];
    for (const [, detail] of state.webappCache) {
      const reads = detail.sections.some(
        (s) => s.state === "ready" && s.matched_dataset === node.id
      );
      if (reads) readers.push(detail.webapp.name);
    }
    // Upstream lineage requires walking edges client-side (already have full edge list).
    const upstream = upstreamOf(node.id);
    Panels.renderNodeDetail(el.drawer, node, { readers, upstream });
    Graph.focusNode(node.id);

    if (node.known_dataset) {
      Api.dataset(state.analysisId, node.id)
        .then((ds) => Panels.renderNodeColumns(el.drawer, ds.columns))
        .catch(() => Panels.renderNodeColumns(el.drawer, []));
    } else {
      const placeholder = el.drawer.querySelector("#node-columns-list");
      if (placeholder) placeholder.textContent = "Not a recognized project dataset (likely a saved model / other recipe I/O, not a schema-bearing dataset).";
    }
  }

  function upstreamOf(datasetId) {
    const byTarget = new Map();
    for (const e of state.graphData.edges) {
      if (!byTarget.has(e.to)) byTarget.set(e.to, []);
      byTarget.get(e.to).push(e.from);
    }
    const seen = new Set([datasetId]);
    const order = [];
    let frontier = [datasetId];
    for (let depth = 0; depth < 6 && frontier.length; depth++) {
      const next = [];
      for (const node of frontier) {
        for (const up of byTarget.get(node) || []) {
          if (!seen.has(up)) { seen.add(up); order.push(up); next.push(up); }
        }
      }
      frontier = next;
    }
    return order;
  }
})();
