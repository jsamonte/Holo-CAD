/**
 * Downloads each GLB the bridge announces and shows it at the right size.
 *
 * Scale is the whole point of this component, so it is worth being explicit
 * about the units involved:
 *
 *   FreeCAD works in millimetres.
 *   The glTF spec says linear distances are metres.
 *   Lens Studio world units are centimetres.
 *
 * Nothing here trusts the units inside the file. The model is instantiated
 * with glTF unit conversion turned off, the combined bounding box of every
 * mesh in the result is measured, and that measurement is compared against
 * the true size that FreeCAD reported in bbox_mm. The ratio is the correction
 * factor, so an exporter that writes millimetres, metres or anything else
 * still ends up the right size.
 *
 * Hierarchy per model id:
 *
 *   <models parent>
 *     holocad_<id>          placement, kept across updates
 *       holocad_<id>_v<n>   uniform scale, replaced on every update
 *         pivot             offset that puts the origin at bottom centre
 *           <glTF root>     whatever the file contained
 */

import {BridgeClient, ModelUpdate, Signal} from "./BridgeClient"

const TAG = "HoloCAD ModelLoader"

/** A measured axis aligned box plus how many meshes went into it. */
type Measured = {
  min: vec3
  max: vec3
  meshes: number
}

export type ShownModel = {
  id: string
  version: number
  /** Per id root that carries the placement. Grab handles and the dimension
   *  overlay attach here, not to the scaled wrapper, which is replaced on
   *  every update. */
  root: SceneObject
  triangles: number
  /** True size reported by FreeCAD. */
  bboxMm: vec3
  /** Size as actually rendered, in millimetres. */
  shownMm: vec3
  /** Correction factor that the measurement produced. */
  correction: number
  /** Requested scale, 1 for true size, 0.1 for 1:10. */
  requested: number
  /** Human readable ratio, for example "1:1" or "1:10". */
  ratioLabel: string
  /** Seconds from the update arriving to the model being visible. */
  loadSeconds: number
}

type ModelEntry = {
  id: string
  root: SceneObject
  current: SceneObject | null
  currentVersion: number
  requestedVersion: number
  placed: boolean
}

@component
export class ModelLoader extends BaseScriptComponent {
  @input
  @hint("Required. Drop the project Internet Module asset here. The require fallback does not resolve.")
  @allowUndefined
  internetModule!: InternetModule

  @input
  @hint("Required. Drop the project Remote Media Module asset here. The require fallback does not resolve.")
  @allowUndefined
  remoteMediaModule!: RemoteMediaModule

  @input
  @hint("PBR material used as the template for every imported mesh. Required.")
  @allowUndefined
  material!: Material

  @input
  @hint("Object holding the BridgeClient. Leave empty to look on this object.")
  @allowUndefined
  bridgeClientObject!: SceneObject

  @input
  @hint("Parent for loaded models. Leave empty to use this object.")
  @allowUndefined
  modelsParent!: SceneObject

  @input
  @hint("Camera used for the first placement. Leave empty to find the main camera.")
  @allowUndefined
  cameraObject!: SceneObject

  @input
  @hint("How far in front of you a model first appears, in cm.")
  spawnDistanceCm: number = 50

  @input
  @hint("How far below eye level a model first appears, in cm.")
  spawnDropCm: number = 15

  @input
  @hint("Warn when the loaded proportions disagree with bbox_mm by more than this fraction.")
  axisTolerance: number = 0.01

  /** Fired every time a model becomes visible, for the status panel. */
  readonly onModelShown = new Signal<ShownModel>()

  /** Fired after a model has been taken out of the scene, with its id. */
  readonly onModelRemoved = new Signal<string>()

  private internet: InternetModule | null = null
  private remoteMedia: RemoteMediaModule | null = null
  private bridge: BridgeClient | null = null
  private entries: Map<string, ModelEntry> = new Map()

  onAwake(): void {
    this.createEvent("OnStartEvent").bind(() => this.start())
  }

  private start(): void {
    this.internet = this.internetModule ?? ModelLoader.tryRequire("InternetModule")
    this.remoteMedia = this.remoteMediaModule ?? ModelLoader.tryRequire("RemoteMediaModule")

    if (this.internet === null || this.internet === undefined) {
      print(`${TAG}: no InternetModule, nothing can be downloaded.`)
      return
    }
    if (this.remoteMedia === null || this.remoteMedia === undefined) {
      print(`${TAG}: no RemoteMediaModule, nothing can be loaded.`)
      return
    }
    if (this.material === null || this.material === undefined) {
      print(
        `${TAG}: the material input is empty. Assign a PBR material, imported meshes need one as a template.`
      )
      return
    }

    this.bridge = this.findBridgeClient()
    if (this.bridge === null) {
      print(`${TAG}: no BridgeClient found. Put one on this object or set bridgeClientObject.`)
      return
    }

    this.bridge.onModelUpdate.add((update) => this.onUpdate(update))
    this.bridge.onModelRemove.add((id) => this.remove(id))
    print(`${TAG}: ready`)
  }

  // ---- loading -----------------------------------------------------------

  private onUpdate(update: ModelUpdate): void {
    if (this.internet === null || this.remoteMedia === null) {
      return
    }

    const entry = this.ensureEntry(update.id)
    if (update.version <= entry.currentVersion) {
      print(
        `${TAG}: ignoring ${update.id} v${update.version}, already showing v${entry.currentVersion}`
      )
      return
    }
    entry.requestedVersion = Math.max(entry.requestedVersion, update.version)

    print(`${TAG}: loading ${update.id} v${update.version} from ${update.url}`)

    let resource: DynamicResource
    try {
      resource = this.internet.makeResourceFromUrl(update.url)
    } catch (e) {
      print(`${TAG}: makeResourceFromUrl failed for ${update.url}: ${e}`)
      return
    }

    this.remoteMedia.loadResourceAsGltfAsset(
      resource,
      (asset: GltfAsset) => this.instantiate(update, entry, asset),
      (error: string) => {
        print(`${TAG}: download failed for ${update.url}: ${error}`)
        print(
          `${TAG}: check that the bridge is running, that the url uses the laptop's LAN ip, and that Experimental APIs are on.`
        )
      }
    )
  }

  private instantiate(update: ModelUpdate, entry: ModelEntry, asset: GltfAsset): void {
    // A newer version may have arrived while this one was downloading.
    if (update.version < entry.requestedVersion) {
      print(`${TAG}: dropping stale download ${update.id} v${update.version}`)
      return
    }

    const staging = global.scene.createSceneObject(`holocad_${update.id}_v${update.version}`)
    staging.setParent(entry.root)
    // Hidden until it has been measured, scaled and recentred, so a model
    // never appears at the wrong size even for one frame.
    staging.enabled = false

    const pivot = global.scene.createSceneObject("pivot")
    pivot.setParent(staging)

    // No unit conversion. The measurement below is what sets the scale, and
    // letting the importer also scale would hide exporter problems.
    const settings = GltfSettings.create()
    settings.convertMetersToCentimeters = false

    asset.tryInstantiateAsync(
      pivot,
      this.material,
      () => this.finish(update, entry, staging, pivot),
      (error: string) => {
        print(`${TAG}: could not instantiate ${update.id} v${update.version}: ${error}`)
        staging.destroy()
      },
      () => {},
      settings
    )
  }

  private finish(
    update: ModelUpdate,
    entry: ModelEntry,
    staging: SceneObject,
    pivot: SceneObject
  ): void {
    if (update.version < entry.requestedVersion) {
      print(`${TAG}: dropping stale instantiation ${update.id} v${update.version}`)
      staging.destroy()
      return
    }

    // pivot is still at identity, so this box is in model space.
    const box = this.measure(pivot)
    if (box.meshes === 0) {
      print(`${TAG}: ${update.id} v${update.version} contains no mesh visuals, nothing to show.`)
      staging.destroy()
      return
    }

    const loaded = box.max.sub(box.min)
    const loadedMax = Math.max(loaded.x, loaded.y, loaded.z)
    if (loadedMax <= 0) {
      print(`${TAG}: ${update.id} measured zero size, giving up.`)
      staging.destroy()
      return
    }

    const bbox = new vec3(update.bbox_mm.x, update.bbox_mm.y, update.bbox_mm.z)
    const targetMaxMm = Math.max(bbox.x, bbox.y, bbox.z)

    let correction = 1
    if (targetMaxMm > 0) {
      // Lens Studio units are centimetres, so the true size in cm is mm / 10.
      correction = targetMaxMm / 10 / loadedMax
      this.checkProportions(update, loaded, bbox, correction)
    } else {
      print(
        `${TAG}: ${update.id} has no bbox_mm, falling back to treating file units as centimetres.`
      )
    }

    const requested = ModelLoader.requestedFactor(update, targetMaxMm)
    const finalScale = correction * requested

    const centre = box.min.add(box.max).uniformScale(0.5)
    // Origin at bottom centre so the model sits flat on a surface.
    pivot.getTransform().setLocalPosition(new vec3(-centre.x, -box.min.y, -centre.z))
    staging.getTransform().setLocalScale(new vec3(finalScale, finalScale, finalScale))

    if (!entry.placed) {
      this.placeInFrontOfUser(entry.root)
      entry.placed = true
    }

    staging.enabled = true
    if (entry.current !== null) {
      entry.current.destroy()
    }
    entry.current = staging
    entry.currentVersion = update.version

    // Size as rendered, in mm, which is what the acceptance test checks.
    const shownMm = loaded.uniformScale(finalScale * 10)
    const loadSeconds = getTime() - update.receivedAt
    const ratioLabel = ModelLoader.ratioLabel(requested)

    print(
      `${TAG}: ${update.id} v${update.version} shown at ${ratioLabel}  ` +
        `size ${shownMm.x.toFixed(1)} x ${shownMm.y.toFixed(1)} x ${shownMm.z.toFixed(1)} mm  ` +
        `(true ${bbox.x.toFixed(1)} x ${bbox.y.toFixed(1)} x ${bbox.z.toFixed(1)} mm)  ` +
        `correction ${correction.toFixed(4)}  meshes ${box.meshes}  ` +
        `load ${(loadSeconds * 1000).toFixed(0)} ms`
    )

    this.onModelShown.emit({
      id: update.id,
      version: update.version,
      root: entry.root,
      triangles: update.triangles,
      bboxMm: bbox,
      shownMm: shownMm,
      correction: correction,
      requested: requested,
      ratioLabel: ratioLabel,
      loadSeconds: loadSeconds
    })
  }

  // ---- measurement -------------------------------------------------------

  /**
   * Combined bounding box of every mesh under root, in root's local space.
   *
   * Transforms are accumulated by hand instead of asking for world
   * transforms, because the hierarchy is still disabled at this point and
   * because a local measurement is independent of wherever the model has
   * been placed.
   */
  private measure(root: SceneObject): Measured {
    let min = new vec3(Infinity, Infinity, Infinity)
    let max = new vec3(-Infinity, -Infinity, -Infinity)
    let meshes = 0

    const visit = (object: SceneObject, toRoot: mat4): void => {
      const visuals = object.getComponents("Component.RenderMeshVisual")
      for (let v = 0; v < visuals.length; v++) {
        const lo = visuals[v].localAabbMin()
        const hi = visuals[v].localAabbMax()
        if (lo === null || hi === null) {
          continue
        }
        meshes++
        for (let corner = 0; corner < 8; corner++) {
          const p = new vec3(
            corner & 1 ? hi.x : lo.x,
            corner & 2 ? hi.y : lo.y,
            corner & 4 ? hi.z : lo.z
          )
          const world = toRoot.multiplyPoint(p)
          min = vec3.min(min, world)
          max = vec3.max(max, world)
        }
      }

      const children = object.getChildrenCount()
      for (let i = 0; i < children; i++) {
        const child = object.getChild(i)
        const t = child.getTransform()
        const local = mat4.compose(t.getLocalPosition(), t.getLocalRotation(), t.getLocalScale())
        visit(child, toRoot.mult(local))
      }
    }

    visit(root, mat4.identity())
    return {min: min, max: max, meshes: meshes}
  }

  /**
   * Sorted proportions of the loaded mesh against the proportions FreeCAD
   * reported. Sorting means an axis swap between Z up and Y up does not
   * trigger it, but a dropped placement or a half exported body does.
   */
  private checkProportions(
    update: ModelUpdate,
    loaded: vec3,
    bbox: vec3,
    correction: number
  ): void {
    const loadedSorted = [loaded.x, loaded.y, loaded.z].sort((a, b) => b - a)
    const targetSorted = [bbox.x, bbox.y, bbox.z].sort((a, b) => b - a)

    for (let i = 0; i < 3; i++) {
      const predictedMm = loadedSorted[i] * correction * 10
      const reference = Math.max(targetSorted[i], 0.001)
      const error = Math.abs(predictedMm - targetSorted[i]) / reference
      if (error > this.axisTolerance) {
        print(
          `${TAG}: WARNING ${update.id} v${update.version} proportions disagree with bbox_mm. ` +
            `Sorted dimensions from the file scale to ` +
            `${loadedSorted.map((d) => (d * correction * 10).toFixed(1)).join(" x ")} mm, ` +
            `FreeCAD reported ${targetSorted.map((d) => d.toFixed(1)).join(" x ")} mm. ` +
            `Largest axis is still correct, but something in the export is off.`
        )
        return
      }
    }
  }

  private static requestedFactor(update: ModelUpdate, targetMaxMm: number): number {
    const spec = update.scale
    if (spec.mode === "ratio") {
      return spec.factor > 0 ? spec.factor : 1
    }
    if (spec.mode === "fit") {
      const target = spec.target_mm ?? 0
      if (target > 0 && targetMaxMm > 0) {
        return target / targetMaxMm
      }
      print(`${TAG}: fit mode needs a positive target_mm and bbox_mm, falling back to 1:1`)
      return 1
    }
    return 1
  }

  private static ratioLabel(requested: number): string {
    if (Math.abs(requested - 1) < 1e-6) {
      return "1:1"
    }
    if (requested < 1) {
      return `1:${(1 / requested).toFixed(requested > 0.01 ? 0 : 1)}`
    }
    return `${requested.toFixed(requested >= 10 ? 0 : 1)}:1`
  }

  // ---- scene plumbing ----------------------------------------------------

  private ensureEntry(id: string): ModelEntry {
    const existing = this.entries.get(id)
    if (existing !== undefined) {
      return existing
    }
    const parent = this.modelsParent ?? this.getSceneObject()
    const root = global.scene.createSceneObject(`holocad_${id}`)
    root.setParent(parent)
    const entry: ModelEntry = {
      id: id,
      root: root,
      current: null,
      currentVersion: 0,
      requestedVersion: 0,
      placed: false
    }
    this.entries.set(id, entry)
    return entry
  }

  /**
   * First placement only. Later updates keep whatever position the model has,
   * which is what makes a live edit look like the part changing rather than
   * the part jumping.
   */
  placeInFrontOfUser(root: SceneObject): void {
    const camera = this.resolveCamera()
    if (camera === null) {
      print(`${TAG}: no camera found, leaving the model at the parent's origin.`)
      return
    }

    const cameraTransform = camera.getTransform()
    const eye = cameraTransform.getWorldPosition()
    // In Lens Studio the direction a camera looks is its transform's back
    // vector, not its forward vector.
    const viewDirection = cameraTransform.back.normalize()

    const position = eye
      .add(viewDirection.uniformScale(this.spawnDistanceCm))
      .add(new vec3(0, -this.spawnDropCm, 0))

    const transform = root.getTransform()
    transform.setWorldPosition(position)

    // Yaw only, so the part stays upright however the head is tilted.
    const toUser = eye.sub(position)
    const flat = new vec3(toUser.x, 0, toUser.z)
    if (flat.length > 0.001) {
      transform.setWorldRotation(quat.lookAt(flat.normalize(), vec3.up()))
    }
  }

  /**
   * Take a model out of the scene, because FreeCAD says it is gone.
   *
   * Deleting or hiding a body would otherwise leave it hanging in the air,
   * since nothing else ever tells the lens it went away.
   */
  remove(id: string): void {
    const entry = this.entries.get(id)
    if (entry === undefined) {
      return
    }
    entry.root.destroy()
    this.entries.delete(id)
    print(`${TAG}: removed ${id}`)
    this.onModelRemoved.emit(id)
  }

  /** Roots of every model currently loaded, by id. */
  roots(): Map<string, SceneObject> {
    const out = new Map<string, SceneObject>()
    this.entries.forEach((entry, id) => out.set(id, entry.root))
    return out
  }

  /** Re-place every model that is currently loaded. */
  replaceAll(): void {
    this.entries.forEach((entry) => {
      this.placeInFrontOfUser(entry.root)
      entry.placed = true
    })
  }

  private findBridgeClient(): BridgeClient | null {
    const host = this.bridgeClientObject ?? this.getSceneObject()
    const onHost = host.getComponent(BridgeClient.getTypeName())
    if (onHost !== null && onHost !== undefined) {
      return onHost
    }
    const count = global.scene.getRootObjectsCount()
    for (let i = 0; i < count; i++) {
      const found = ModelLoader.searchForBridgeClient(global.scene.getRootObject(i))
      if (found !== null) {
        return found
      }
    }
    return null
  }

  private static searchForBridgeClient(object: SceneObject): BridgeClient | null {
    const client = object.getComponent(BridgeClient.getTypeName())
    if (client !== null && client !== undefined) {
      return client
    }
    for (let i = 0; i < object.getChildrenCount(); i++) {
      const found = ModelLoader.searchForBridgeClient(object.getChild(i))
      if (found !== null) {
        return found
      }
    }
    return null
  }

  private resolveCamera(): Camera | null {
    if (this.cameraObject !== null && this.cameraObject !== undefined) {
      const camera = this.cameraObject.getComponent("Component.Camera") as Camera
      if (camera !== null) {
        return camera
      }
    }
    const count = global.scene.getRootObjectsCount()
    for (let i = 0; i < count; i++) {
      const found = ModelLoader.searchForCamera(global.scene.getRootObject(i))
      if (found !== null) {
        return found
      }
    }
    return null
  }

  private static searchForCamera(object: SceneObject): Camera | null {
    const camera = object.getComponent("Component.Camera") as Camera
    if (camera !== null && camera !== undefined) {
      return camera
    }
    for (let i = 0; i < object.getChildrenCount(); i++) {
      const found = ModelLoader.searchForCamera(object.getChild(i))
      if (found !== null) {
        return found
      }
    }
    return null
  }

  /**
   * Kept as a last resort only. On Lens Studio 5.15 this does not resolve for
   * InternetModule or RemoteMediaModule: the build logs "Failed to resolve
   * dependency" to the log file and the require yields nothing, whether or not
   * the module assets exist. The inspector inputs are the path that works.
   */
  private static tryRequire(name: string): any {
    try {
      return require(`LensStudio:${name}`)
    } catch (e) {
      return null
    }
  }
}
