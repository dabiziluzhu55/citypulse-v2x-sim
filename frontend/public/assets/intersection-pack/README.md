# Intersection asset conversion contract

Source candidate: `E:\city\model\路口.rar`.

The archive contains a 3ds Max 15.00 scene, V-Ray 3.60.03 materials and five
`.vrmesh` proxies. Browsers cannot load those formats. Convert only reusable
street furniture, vegetation and material assets to glTF 2.0 (`.glb`). The
SUMO-derived road, lane, stop-line and crosswalk geometry remains authoritative.

## Required export contract

- Units: meters.
- Up axis: Z-up in the source; export with the transform baked for glTF.
- Origin: the asset footprint center at `(0, 0, 0)`.
- Materials: PBR metallic-roughness with embedded or relative textures.
- Textures: power-of-two dimensions, sRGB for base color/emissive, linear for
  normal/roughness/metalness.
- Geometry: no cameras, lights, animation controllers or V-Ray dependencies.
- Optimization: remove hidden geometry, merge by material, generate normals and
  tangents, and apply Meshopt or Draco only after an uncompressed GLB validates.
- Licensing: add the verified source and redistribution terms before enabling an
  asset in an intersection environment manifest.

## Runtime placement

Add a `detailModel` object to an intersection `environment.json` only after the
GLB passes visual and license review:

```json
{
  "detailModel": {
    "url": "/assets/intersection-pack/demo_2-detail.glb",
    "position": [116.126756, 38.99115, 0],
    "rotation": [1.5707963267948966, 0, 0],
    "scale": 1
  }
}
```

Model replacement is atomic: the current detail model stays visible until the
new GLB has loaded and stale intersection requests are discarded.
