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
 *     holocad_assembly      one transform for the whole assembly
 *       holocad_<id>        placement, kept across updates
 *         holocad_<id>_v<n> uniform scale, replaced on every update
 *           pivot           offset that puts the origin at bottom centre
 *             <glTF root>   whatever the file contained
 */

import {BridgeClient, ModelUpdate, Signal} from "./BridgeClient"

const TAG = "HoloCAD ModelLoader"
const ASSEMBLY = "holocad_assembly"

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
  /** Where this part belongs inside the assembly, in cm. */
  offsetCm: vec3
}

type ModelEntry = {
  id: string
  root: SceneObject
  current: SceneObject | null
  currentVersion: number
  requestedVersion: number
  offsetCm: vec3
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
  // 3%, not 1%: tessellating a 2.6 mm sphere into flat facets loses about
  // 1.5% of its minor axes, so 1% warned about every small round feature
  // and buried the one case worth seeing.
  axisTolerance: number = 0.03

  /** Fired every time a model becomes visible, for the status panel. */
  readonly onModelShown = new Signal<ShownModel>()

  /** Fired after a model has been taken out of the scene, with its id. */
  readonly onModelRemoved = new Signal<string>()

  private internet: InternetModule | null = null
  private remoteMedia: RemoteMediaModule | null = null
  private bridge: BridgeClient | null = null
  private entries: Map<string, ModelEntry> = new Map()
  private assembly: SceneObject | null = null
  private assemblyPlaced: boolean = false

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

    // The addon states how many Lens Studio units one file unit is worth,
    // because it wrote the file. That is used in preference to measuring
    // the loaded mesh, which is not trustworthy: across a real 21 part
    // assembly the measured box came out two to five times too large for
    // most parts, so each was scaled by a wrong factor of its own and the
    // assembly fell apart. The measurement is kept as a cross check.
    let correction = 1
    if (update.cm_per_unit > 0) {
      correction = update.cm_per_unit
      this.checkStatedScale(update, loadedMax, targetMaxMm, correction)
    } else if (targetMaxMm > 0) {
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

    // The addon writes each mesh about its own bottom centre already, so
    // the pivot stays at the origin. It used to be recentred here from the
    // measured box, which in per body mode centred every part separately
    // and dropped the whole assembly on one spot.
    const centre = box.min.add(box.max).uniformScale(0.5)
    if (!this.centredByAddon(update)) {
      pivot.getTransform().setLocalPosition(
        new vec3(-centre.x, -box.min.y, -centre.z))
    }
    staging.getTransform().setLocalScale(new vec3(finalScale, finalScale, finalScale))

    // Where the part belongs within the assembly. The offset arrives in
    // millimetres, Lens Studio works in centimetres, and a model asked for
    // at 1:10 has to be laid out at 1:10 too or the assembly comes apart.
    const offsetCm = (update.offset_mm ?? vec3.zero()).uniformScale(requested / 10)
    entry.offsetCm = offsetCm
    entry.root.getTransform().setLocalPosition(offsetCm)

    // Before it is revealed, so a part is never seen in the wrong colour.
    this.tint(pivot, update)

    if (!this.assemblyPlaced) {
      this.placeAssemblyInFrontOfUser()
      this.assemblyPlaced = true
    }

    staging.enabled = true
    if (entry.current !== null) {
      entry.current.destroy()
    }
    entry.current = staging
    entry.currentVersion = update.version

    // Size as rendered, in mm, which is what the acceptance test checks,
    // and what the dimension box and the grab collider are built from.
    //
    // Taken from the size FreeCAD reported rather than from the measured
    // mesh, for the same reason the scale is: the measurement was wrong for
    // most parts, which made both the wireframe and the grab box the wrong
    // size as well. bbox_mm is in document axes, so it needs the same Z up
    // to Y up turn as the geometry: document Z becomes height.
    const shownMm = ModelLoader.statedSize(update, bbox, requested, loaded, finalScale)
    const loadSeconds = getTime() - update.receivedAt
    const ratioLabel = ModelLoader.ratioLabel(requested)

    print(
      `${TAG}: ${update.id} v${update.version} shown at ${ratioLabel}  ` +
        `size ${shownMm.x.toFixed(1)} x ${shownMm.y.toFixed(1)} x ${shownMm.z.toFixed(1)} mm  ` +
        `(true ${bbox.x.toFixed(1)} x ${bbox.y.toFixed(1)} x ${bbox.z.toFixed(1)} mm)  ` +
        `correction ${correction.toFixed(4)}  meshes ${box.meshes}  ` +
        `at ${offsetCm.x.toFixed(2)}, ${offsetCm.y.toFixed(2)}, ` +
        `${offsetCm.z.toFixed(2)} cm  ` +
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
      loadSeconds: loadSeconds,
      offsetCm: offsetCm
    })
  }

  /**
   * The rendered size in millimetres, from whichever source can be trusted.
   *
   * With a stated scale the true size is known, so it is used directly,
   * turned into the lens's axes. Without one the lens is measuring anyway
   * and the measured box is all there is.
   */
  private static statedSize(
    update: ModelUpdate,
    bbox: vec3,
    requested: number,
    loaded: vec3,
    finalScale: number
  ): vec3 {
    if (update.cm_per_unit > 0 && Math.max(bbox.x, bbox.y, bbox.z) > 0) {
      // Document X stays X, document Z is the height, document Y is depth.
      return new vec3(bbox.x, bbox.z, bbox.y).uniformScale(requested)
    }
    return loaded.uniformScale(finalScale * 10)
  }

  /**
   * Warn when the stated scale and the loaded mesh disagree badly.
   *
   * Not a failure: the measurement is the less trustworthy of the two, which
   * is why it is no longer in charge. But a tenfold disagreement would mean
   * the exporter and the lens have genuinely diverged, and that is worth
   * seeing rather than silently drawing the part at the stated size.
   */
  private checkStatedScale(
    update: ModelUpdate,
    loadedMax: number,
    targetMaxMm: number,
    correction: number
  ): void {
    if (loadedMax <= 0 || targetMaxMm <= 0) {
      return
    }
    const wouldBeMm = loadedMax * correction * 10
    const ratio = wouldBeMm / targetMaxMm
    if (ratio > 1.5 || ratio < 0.67) {
      print(
        `${TAG}: NOTE ${update.id} measures ${wouldBeMm.toFixed(1)} mm at the ` +
          `stated scale but FreeCAD reports ${targetMaxMm.toFixed(1)} mm. ` +
          `Drawing it at the size FreeCAD reports.`
      )
    }
  }

  /**
   * Whether the addon centred the mesh and told us where it belongs.
   *
   * An offset of exactly zero is the normal case for a single model, which
   * is its own origin, so the test is whether the field arrived at all
   * rather than whether it is non zero. An older addon sends neither, and
   * those models still get recentred here.
   */
  private centredByAddon(update: ModelUpdate): boolean {
    return update.offset_mm !== null
  }

  // ---- colour ------------------------------------------------------------

  /**
   * Give each mesh the colour FreeCAD gave the object it came from.
   *
   * The GLB carries these already, as baseColorFactor per material, but
   * Lens Studio instantiates glTF against the one template material passed
   * to tryInstantiateAsync and the file's own colours did not come through:
   * every part arrived in the template's colour. So the addon sends the
   * colours beside the model and they are applied here.
   *
   * Each visual gets its own clone of the template. Without the clone they
   * would share one material and the last colour set would win for the
   * whole model, which is the same bug in a different place.
   */
  private tint(pivot: SceneObject, update: ModelUpdate): void {
    const colours = update.colours
    if (colours === undefined || colours.length === 0) {
      return
    }
    const visuals: any[] = []
    ModelLoader.collectVisuals(pivot, visuals)
    if (visuals.length === 0) {
      return
    }

    let applied = 0
    for (let i = 0; i < visuals.length; i++) {
      // One colour for every mesh when only one was sent, which is the
      // per-body case and the default. Otherwise mesh order, which is the
      // order the exporter writes them in.
      const rgba = colours.length === 1 ? colours[0] : colours[i]
      if (rgba === undefined) {
        continue
      }
      const colour = new vec4(rgba[0], rgba[1], rgba[2], rgba[3])
      const visual = visuals[i]
      try {
        const material = visual.mainMaterial.clone()
        if (!ModelLoader.setBaseColour(material, colour)) {
          print(
            `${TAG}: the material has no colour input, so ${update.id} cannot ` +
              `be tinted. Assign a glTF material to ModelLoader's material input.`
          )
          return
        }
        visual.clearMaterials()
        visual.addMaterial(material)
        applied++
      } catch (e) {
        print(`${TAG}: could not tint a mesh of ${update.id}: ${e}`)
        return
      }
    }
    print(
      `${TAG}: ${update.id} tinted ${applied} of ${visuals.length} mesh(es) ` +
        `from ${colours.length} colour(s)`
    )
  }

  /**
   * Put a colour on a material, whichever colour input it happens to have.
   *
   * Materials differ in what they call this, and getting it wrong is silent:
   * assigning an unknown property on a pass does nothing and raises nothing,
   * so the part simply stays the template's colour. The project's own
   * material is built from the glTF preset, whose input is
   * baseColorFactor, matching the glTF spec. baseColor covers the Simple
   * PBR materials. A material with neither, such as the Spectacles
   * template's textured PBR graph, cannot be tinted at all, and that is
   * worth saying out loud rather than leaving every part grey.
   */
  private static setBaseColour(material: any, colour: vec4): boolean {
    const pass = material.mainPass
    if (pass === undefined || pass === null) {
      return false
    }
    if (pass.baseColorFactor !== undefined) {
      pass.baseColorFactor = colour
      return true
    }
    if (pass.baseColor !== undefined) {
      pass.baseColor = colour
      return true
    }
    return false
  }

  private static collectVisuals(object: SceneObject, into: any[]): void {
    const found = object.getComponents("Component.RenderMeshVisual")
    for (let i = 0; i < found.length; i++) {
      into.push(found[i])
    }
    for (let i = 0; i < object.getChildrenCount(); i++) {
      ModelLoader.collectVisuals(object.getChild(i), into)
    }
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
    const root = global.scene.createSceneObject(`holocad_${id}`)
    root.setParent(this.assemblyRoot())
    const entry: ModelEntry = {
      id: id,
      root: root,
      current: null,
      currentVersion: 0,
      requestedVersion: 0,
      offsetCm: vec3.zero()
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

  /**
   * One object every model hangs from, so the whole assembly can be moved
   * as a piece.
   *
   * Deliberately not the models parent itself. That object also carries the
   * script components and the panels, and grabbing it would drag the status
   * display around with the parts. This is a dedicated child with nothing
   * on it but models, created once and never replaced, so a transform put
   * on it by the hands survives every model update.
   */
  assemblyRoot(): SceneObject {
    if (this.assembly !== null) {
      return this.assembly
    }
    const parent = this.modelsParent ?? this.getSceneObject()
    const existing = ModelLoader.childNamed(parent, ASSEMBLY)
    this.assembly = existing ?? global.scene.createSceneObject(ASSEMBLY)
    if (existing === null) {
      this.assembly.setParent(parent)
    }
    return this.assembly
  }

  /** Put the assembly transform back to where it started, scale included. */
  resetAssembly(): void {
    this.resetAssemblyPlacement()
    this.assemblyRoot().getTransform().setLocalScale(new vec3(1, 1, 1))
  }

  /**
   * Undo where the assembly has been dragged and turned, but not its size.
   *
   * Kept apart from resetAssembly so that bringing the parts back within
   * reach does not also throw away a scale somebody chose on purpose.
   */
  resetAssemblyPlacement(): void {
    const transform = this.assemblyRoot().getTransform()
    transform.setLocalPosition(vec3.zero())
    transform.setLocalRotation(quat.quatIdentity())
  }

  private static childNamed(parent: SceneObject, name: string): SceneObject | null {
    for (let i = 0; i < parent.getChildrenCount(); i++) {
      const child = parent.getChild(i)
      if (child.name === name) {
        return child
      }
    }
    return null
  }

  /** Roots of every model currently loaded, by id. */
  roots(): Map<string, SceneObject> {
    const out = new Map<string, SceneObject>()
    this.entries.forEach((entry, id) => out.set(id, entry.root))
    return out
  }

  /**
   * Bring the assembly back in front of the user.
   *
   * Deliberately the assembly and not each part: the parts' own positions
   * are the shape of the assembly, so re-placing them individually would
   * collapse the model onto one point, which is the bug this replaced.
   */
  replaceAll(): void {
    this.placeAssemblyInFrontOfUser()
    this.assemblyPlaced = true
  }

  /** Put each part back where the addon said it belongs. */
  restoreOffsets(): void {
    this.entries.forEach((entry) => {
      entry.root.getTransform().setLocalPosition(entry.offsetCm)
    })
  }

  /** Place the whole assembly where it can be seen, once. */
  placeAssemblyInFrontOfUser(): void {
    this.placeInFrontOfUser(this.assemblyRoot())
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
