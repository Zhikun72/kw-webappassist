// Flow graph visualization: force-directed node-link diagram over datasets,
// zone color-coding (fixed categorical order, "Other" fold beyond 8 slots -
// see project's dataviz reference), terminal-node highlight, zoom/pan,
// hover tooltip, legend-driven toggle-to-isolate.
const Graph = (() => {
  const CAT_VARS = ["--cat-1", "--cat-2", "--cat-3", "--cat-4", "--cat-5", "--cat-6", "--cat-7", "--cat-8"];
  const MAX_ZONE_COLORS = 8;

  let svg, g, simulation, zoomBehavior;
  let nodeSel, linkSel;
  let currentNodes = [], currentEdges = [];
  let zoneColorMap = {}; // zoneId -> css var name
  let onNodeClickCb = null;
  let isolatedZone = null;

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function assignZoneColors(nodes) {
    const counts = {};
    for (const n of nodes) {
      if (n.zone_id) counts[n.zone_id] = (counts[n.zone_id] || 0) + 1;
    }
    const ordered = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([id]) => id);
    const map = {};
    ordered.forEach((zoneId, i) => {
      map[zoneId] = i < MAX_ZONE_COLORS ? CAT_VARS[i] : "--cat-other";
    });
    return map;
  }

  function colorForNode(n) {
    if (!n.zone_id) return cssVar("--cat-other");
    return cssVar(zoneColorMap[n.zone_id] || "--cat-other");
  }

  function zoneNameOf(n) {
    return n.zone_name || "(no zone)";
  }

  function showTooltip(evt, html) {
    const tip = document.getElementById("tooltip");
    tip.innerHTML = html;
    tip.classList.add("is-visible");
    moveTooltip(evt);
  }
  function moveTooltip(evt) {
    const tip = document.getElementById("tooltip");
    const pad = 14;
    let x = evt.clientX + pad, y = evt.clientY + pad;
    if (x + 260 > window.innerWidth) x = evt.clientX - 260 - pad;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  function hideTooltip() {
    document.getElementById("tooltip").classList.remove("is-visible");
  }

  function init(svgEl, { onNodeClick } = {}) {
    onNodeClickCb = onNodeClick || null;
    svg = d3.select(svgEl);
    svg.selectAll("*").remove();
    g = svg.append("g").attr("class", "graph-root");
    g.append("g").attr("class", "links");
    g.append("g").attr("class", "nodes");

    zoomBehavior = d3.zoom()
      .scaleExtent([0.15, 4])
      .on("zoom", (event) => g.attr("transform", event.transform));
    svg.call(zoomBehavior);

    document.getElementById("btn-zoom-in").onclick = () => svg.transition().call(zoomBehavior.scaleBy, 1.3);
    document.getElementById("btn-zoom-out").onclick = () => svg.transition().call(zoomBehavior.scaleBy, 1 / 1.3);
    document.getElementById("btn-zoom-reset").onclick = () => svg.transition().call(zoomBehavior.transform, d3.zoomIdentity);
  }

  function render(data) {
    currentNodes = data.nodes.map((n) => ({ ...n }));
    currentEdges = data.edges.map((e) => ({ ...e }));
    zoneColorMap = assignZoneColors(currentNodes);

    const nodeById = new Map(currentNodes.map((n) => [n.id, n]));
    const links = currentEdges
      .filter((e) => nodeById.has(e.from) && nodeById.has(e.to))
      .map((e) => ({ source: e.from, target: e.to, recipe: e.recipe }));

    const bounds = svg.node().getBoundingClientRect();
    const width = bounds.width || 800, height = bounds.height || 600;

    simulation = d3.forceSimulation(currentNodes)
      .force("link", d3.forceLink(links).id((d) => d.id).distance(46).strength(0.35))
      .force("charge", d3.forceManyBody().strength(-70))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collide", d3.forceCollide().radius(10))
      .alphaDecay(0.02);

    linkSel = g.select(".links")
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke", "var(--border-strong)")
      .attr("stroke-width", 1)
      .attr("opacity", 0.5);

    nodeSel = g.select(".nodes")
      .selectAll("g.node")
      .data(currentNodes, (d) => d.id)
      .join((enter) => {
        const gg = enter.append("g").attr("class", "node").style("cursor", "pointer");
        gg.append("circle");
        return gg;
      });

    nodeSel.select("circle")
      .attr("r", (d) => (d.terminal ? 7 : 5))
      .attr("fill", (d) => colorForNode(d))
      .attr("stroke", (d) => (d.terminal ? "var(--text-primary)" : "var(--surface-1)"))
      .attr("stroke-width", (d) => (d.terminal ? 2 : 1.2))
      .attr("opacity", (d) => (d.known_dataset ? 1 : 0.35));

    nodeSel
      .on("mouseenter", (evt, d) => {
        showTooltip(
          evt,
          `<div class="viz-tooltip__title">${d.id}</div>` +
            `<div class="viz-tooltip__row">${d.type} &middot; ${d.column_count} columns</div>` +
            `<div class="viz-tooltip__row">Zone: ${zoneNameOf(d)}</div>` +
            `<div class="viz-tooltip__row">In: ${d.in_degree} &middot; Out: ${d.out_degree}${d.terminal ? " (terminal)" : ""}</div>`
        );
      })
      .on("mousemove", moveTooltip)
      .on("mouseleave", hideTooltip)
      .on("click", (evt, d) => {
        hideTooltip();
        if (onNodeClickCb) onNodeClickCb(d);
      })
      .call(
        d3.drag()
          .on("start", (event, d) => {
            if (!event.active) simulation.alphaTarget(0.2).restart();
            d.fx = d.x; d.fy = d.y;
          })
          .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
          .on("end", (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null; d.fy = null;
          })
      );

    simulation.on("tick", () => {
      linkSel
        .attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y)
        .attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
      nodeSel.attr("transform", (d) => `translate(${d.x},${d.y})`);
    });

    setTimeout(() => simulation && simulation.stop(), 4000);

    renderLegend();
  }

  function renderLegend() {
    const counts = {};
    const names = {};
    for (const n of currentNodes) {
      const key = n.zone_id || "__none__";
      counts[key] = (counts[key] || 0) + 1;
      names[key] = n.zone_id ? zoneNameOf(n) : "(no zone)";
    }
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    const host = document.getElementById("canvas-legend");
    const rows = entries
      .slice(0, MAX_ZONE_COLORS + 1)
      .map(([zoneId, count]) => {
        const colorVar = zoneId === "__none__" ? "--cat-other" : zoneColorMap[zoneId] || "--cat-other";
        const dimmed = isolatedZone && isolatedZone !== zoneId ? "is-dimmed" : "";
        return `<div class="legend__item ${dimmed}" data-zone="${zoneId}">
          <span class="legend__swatch" style="background:var(${colorVar})"></span>
          <span class="legend__label">${names[zoneId]} (${count})</span>
        </div>`;
      })
      .join("");
    const extra = entries.length > MAX_ZONE_COLORS + 1
      ? `<div class="legend__item"><span class="legend__swatch" style="background:var(--cat-other)"></span><span class="legend__label">+${entries.length - MAX_ZONE_COLORS - 1} more zones (folded to Other)</span></div>`
      : "";
    host.innerHTML = `<div class="legend__title">Flow Zones &middot; click to isolate</div>${rows}${extra}
      <div style="margin-top:6px;padding-top:6px;border-top:1px solid var(--gridline);display:flex;align-items:center;gap:6px;">
        <svg width="14" height="14"><circle cx="7" cy="7" r="6" fill="none" stroke="var(--text-primary)" stroke-width="2"/></svg>
        <span class="legend__label">Terminal dataset</span>
      </div>`;

    host.querySelectorAll(".legend__item[data-zone]").forEach((el) => {
      el.addEventListener("click", () => {
        const zoneId = el.getAttribute("data-zone");
        isolatedZone = isolatedZone === zoneId ? null : zoneId;
        applyIsolation();
        renderLegend();
      });
    });
  }

  function applyIsolation() {
    nodeSel.select("circle").attr("opacity", (d) => {
      if (!d.known_dataset) return 0.15;
      if (!isolatedZone) return 1;
      const key = d.zone_id || "__none__";
      return key === isolatedZone ? 1 : 0.12;
    });
    linkSel.attr("opacity", (d) => {
      if (!isolatedZone) return 0.5;
      const srcZone = (d.source.zone_id || "__none__");
      const tgtZone = (d.target.zone_id || "__none__");
      return srcZone === isolatedZone || tgtZone === isolatedZone ? 0.6 : 0.05;
    });
  }

  function focusNode(nodeId) {
    const n = currentNodes.find((d) => d.id === nodeId);
    if (!n || n.x == null) return;
    const bounds = svg.node().getBoundingClientRect();
    const t = d3.zoomIdentity
      .translate(bounds.width / 2, bounds.height / 2)
      .scale(1.4)
      .translate(-n.x, -n.y);
    svg.transition().duration(400).call(zoomBehavior.transform, t);

    nodeSel.select("circle").attr("stroke", (d) =>
      d.id === nodeId ? "var(--cat-1)" : d.terminal ? "var(--text-primary)" : "var(--surface-1)"
    ).attr("stroke-width", (d) => (d.id === nodeId ? 3 : d.terminal ? 2 : 1.2));
  }

  return { init, render, focusNode };
})();
