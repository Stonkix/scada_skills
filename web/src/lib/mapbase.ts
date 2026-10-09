import maplibregl from 'maplibre-gl'
import type { ObjectsResponse } from '@/api/types'
import { lngLatRing, toLngLat } from './geo'
import { GEOZONE_STYLE } from './plan'
import { SiteLayer, arrowIcon, baseStyle, metresToPixels } from './realmap'

/**
 * The region map without the live dashboard around it: basemap, sites, roads, highways, geozones and the
 * 3D buildings. Screens that show something else on the same map (replay, heatmap) start from here.
 */
export interface BaseMap {
  map: maplibregl.Map
  layer: SiteLayer
  fitRegion(animate?: boolean): void
  fitSite(siteId: string): void
  destroy(): void
}

type Pt = [number, number]
type FC = GeoJSON.FeatureCollection
export const fc = (features: GeoJSON.Feature[]): FC => ({ type: 'FeatureCollection', features })

export function createBaseMap(el: HTMLElement, o: ObjectsResponse, onReady: (b: BaseMap) => void): BaseMap {
  const g = o.georef
  const line = (pts: Pt[], props: Record<string, unknown> = {}): GeoJSON.Feature => ({
    type: 'Feature', properties: props, geometry: { type: 'LineString', coordinates: pts.map(([x, y]) => toLngLat(g, x, y)) },
  })
  const polygon = (ring: Pt[], props: Record<string, unknown> = {}): GeoJSON.Feature => ({
    type: 'Feature', properties: props, geometry: { type: 'Polygon', coordinates: [lngLatRing(g, ring)] },
  })
  const bounds = (pts: Pt[]) => {
    const ll = pts.map(([x, y]) => toLngLat(g, x, y))
    return ll.reduce((b, p) => b.extend(p), new maplibregl.LngLatBounds(ll[0], ll[0]))
  }

  const map = new maplibregl.Map({ container: el, style: baseStyle(), center: toLngLat(g, 0, 0), zoom: 10, maxPitch: 75,
    attributionControl: { compact: true } })
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right')
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right')
  const layer = new SiteLayer(g)
  if (import.meta.env.DEV) (window as unknown as { __base: maplibregl.Map }).__base = map // console debugging
  let ready = false
  let userMoved = false

  const fitRegion = (animate = false) => {
    const cam = map.cameraForBounds(bounds(o.sites.flatMap((s) => s.geometry.coordinates[0] as Pt[])),
      { padding: { top: 70, bottom: 40, left: 50, right: 50 }, bearing: 0 })
    if (cam) map[animate ? 'easeTo' : 'jumpTo']({ ...cam, pitch: 35, bearing: 0, ...(animate ? { duration: 900 } : {}) })
  }
  const fitSite = (siteId: string) => {
    const s = o.sites.find((x) => x.id === siteId)
    if (!s) return
    userMoved = true
    const cam = map.cameraForBounds(bounds(s.geometry.coordinates[0] as Pt[]), { padding: 60 })
    if (cam) map.easeTo({ ...cam, pitch: 50, bearing: -15, duration: 900 })
  }
  // the grid cell settles after the map is created: keep the region framed until the user moves the camera
  const resizer = new ResizeObserver(() => {
    map.resize()
    if (ready && !userMoved) fitRegion()
  })
  resizer.observe(el)
  map.on('movestart', (e) => {
    if ((e as { originalEvent?: Event }).originalEvent) userMoved = true
  })

  const base: BaseMap = {
    map, layer, fitRegion, fitSite,
    destroy() {
      resizer.disconnect()
      map.remove()
    },
  }
  // not 'load': that waits for the first basemap tiles
  map.once('style.load', () => {
    const w = (z: number) => ['*', ['get', 'width_m'], metresToPixels(g.origin_lat, z, 1)]
    map.addSource('sites', { type: 'geojson', data: fc(o.sites.map((s) => polygon(s.geometry.coordinates[0] as Pt[]))) })
    map.addSource('roads', { type: 'geojson', data: fc(o.roads.filter((r) => r.road_class !== 'public')
      .map((r) => line(r.geometry.coordinates as Pt[], { width_m: r.width_m }))) })
    map.addSource('routes', { type: 'geojson', data: fc(o.roads.filter((r) => r.road_class === 'public')
      .map((r) => line(r.geometry.coordinates as Pt[]))) })
    map.addSource('zones', { type: 'geojson', data: fc(o.geozones.filter((z) => z.zone_type !== 'speed')
      .map((z) => polygon(z.geometry.coordinates[0] as Pt[], { color: GEOZONE_STYLE[z.zone_type]?.color ?? '#a3a3a3' }))) })
    map.addLayer({ id: 'routes', type: 'line', source: 'routes', layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#38bdf8', 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 1.5, 16, 4], 'line-opacity': 0.45 } })
    map.addLayer({ id: 'site-fill', type: 'fill', source: 'sites', paint: { 'fill-color': '#3a4150', 'fill-opacity': 0.92 } })
    map.addLayer({ id: 'site-line', type: 'line', source: 'sites', paint: { 'line-color': '#38bdf8', 'line-width': 2, 'line-dasharray': [3, 2] } })
    map.addLayer({ id: 'zones-fill', type: 'fill', source: 'zones', minzoom: 15, paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.12 } })
    map.addLayer({ id: 'roads', type: 'line', source: 'roads', layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#1c222d', 'line-width': ['interpolate', ['exponential', 2], ['zoom'], 12, w(12), 22, w(22)] as never } })
    map.addImage('arrow', arrowIcon(), { sdf: true })
    map.addLayer(layer)
    layer.setPlan(o)
    fitRegion()
    ready = true
    onReady(base)
  })
  return base
}
