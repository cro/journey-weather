/* <route-map>: Leaflet + OSM tiles, colored pieces, numbered stop markers (PLAN.md §5). */

const COLORS = {
  clear: "#2f7d55",
  warning: "#d89022",
  severe: "#c84b3f",
  nodata: "#87909a",
};

class RouteMap extends HTMLElement {
  static observedAttributes = ["data-route", "selected"];

  #map;
  #group;
  #pieces = [];
  #ro;

  connectedCallback() {
    this.#map = L.map(this);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap contributors",
      maxZoom: 19,
    }).addTo(this.#map);
    this.#group = L.layerGroup().addTo(this.#map);
    // the element may be display:none until the first plan; keep Leaflet sized right
    this.#ro = new ResizeObserver(() => this.#map?.invalidateSize());
    this.#ro.observe(this);
    this.#render();
  }

  disconnectedCallback() {
    this.#ro?.disconnect();
    this.#map?.remove();
    this.#map = undefined;
  }

  attributeChangedCallback(name) {
    if (!this.#map) return;
    if (name === "data-route") this.#render();
    else if (name === "selected") this.#highlight(this.#selected());
  }

  #selected() {
    const v = Number(this.getAttribute("selected"));
    return Number.isFinite(v) ? v : -1;
  }

  #render() {
    this.#group.clearLayers();
    this.#pieces = [];
    let data;
    try {
      data = JSON.parse(this.getAttribute("data-route") || "{}");
    } catch {
      return;
    }
    const bounds = [];
    for (const p of data.pieces ?? []) {
      const line = L.polyline(p.coords, {
        weight: 5,
        color: COLORS[p.level] ?? COLORS.nodata,
      })
        .on("click", () => this.#select(p.row))
        .addTo(this.#group);
      this.#pieces.push({ row: p.row, line });
      bounds.push(...p.coords);
    }
    for (const s of data.stops ?? []) {
      L.circleMarker([s.lat, s.lon], {
        radius: 9,
        fillColor: COLORS[s.level] ?? COLORS.nodata,
        fillOpacity: 1,
        color: "#fff",
        weight: 2,
      })
        .on("click", () => this.#select(s.row))
        .bindTooltip(String(s.n), { permanent: true, direction: "center" })
        .addTo(this.#group);
      bounds.push([s.lat, s.lon]);
    }
    if (bounds.length) this.#map.fitBounds(bounds, { padding: [24, 24] });
    this.#highlight(this.#selected());
  }

  #highlight(row) {
    let mid;
    for (const p of this.#pieces) {
      p.line.setStyle({ weight: p.row === row ? 9 : 5 });
      if (p.row === row) mid = p.line;
    }
    if (mid) {
      const pts = mid.getLatLngs();
      this.#map.panTo(pts[Math.floor(pts.length / 2)]);
    }
  }

  #select(row) {
    this.dispatchEvent(new CustomEvent("row-select", { detail: { row }, bubbles: true }));
  }
}

customElements.define("route-map", RouteMap);
