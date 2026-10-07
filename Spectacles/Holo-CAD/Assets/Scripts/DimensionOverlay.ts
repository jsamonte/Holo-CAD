/**
 * A wireframe box around the model, labelled with its true size in mm and
 * the scale it is being shown at.
 *
 * The box is built at runtime with MeshBuilder in Lines topology, because
 * the model only exists once FreeCAD has sent it, so there is no mesh asset
 * to prepare in the editor.
 *
 * The labels read the true size, not the rendered size. At 1:1 those are the
 * same number, which is the point of the acceptance test, and at 1:10 the
 * label still says what the part really measures.
 */

import {ModelLoader, ShownModel} from "./ModelLoader"

const TAG = "HoloCAD DimensionOverlay"
const OVERLAY = "holocad_dimensions"

/** The 12 edges of a unit cube, as pairs of corner indices. */
const EDGES = [
  [0, 1], [1, 3], [3, 2], [2, 0],
  [4, 5], [5, 7], [7, 6], [6, 4],
  [0, 4], [1, 5], [2, 6], [3, 7]
]

@component
export class DimensionOverlay extends BaseScriptComponent {
  @input
  @hint("Object holding the ModelLoader. Leave empty to look on this object.")
  @allowUndefined
  modelLoaderObject!: SceneObject

  @input
  @hint("Unlit material for the wireframe. Without one the box is skipped.")
  @allowUndefined
  lineMaterial!: Material

  @input
  @hint("Show the box as soon as a model arrives.")
  visibleAtStart: boolean = false

  @input
  @hint("Label size in cm. Labels always face you.")
  labelSizeCm: number = 2.0

  @input
  @hint("Camera the labels turn to face. Leave empty to find the main camera.")
  @allowUndefined
  cameraObject!: SceneObject

  private loader: ModelLoader | null = null
  private camera: SceneObject | null = null
  private shown: Map<string, ShownModel> = new Map()
  private overlays: Map<string, SceneObject> = new Map()
  private visible: boolean = false

  onAwake(): void {
    this.createEvent("OnStartEvent").bind(() => this.start())
  }

  private start(): void {
    this.visible = this.visibleAtStart
    this.camera = this.resolveCamera()
    const host = this.modelLoaderObject ?? this.getSceneObject()
    this.loader = host.getComponent(ModelLoader.getTypeName())
    if (this.loader === null) {
      print(`${TAG}: no ModelLoader found, nothing to measure.`)
      return
    }
    this.loader.onModelShown.add((model) => this.onModelShown(model))
    this.loader.onModelRemoved.add((id) => this.forget(id))
    print(`${TAG}: ready, overlay ${this.visible ? "on" : "off"}`)
  }

  /** Flip the overlay. Wire this to a button. */
  toggle(): boolean {
    this.setVisible(!this.visible)
    return this.visible
  }

  setVisible(visible: boolean): void {
    this.visible = visible
    this.overlays.forEach((overlay) => {
      overlay.enabled = visible
    })
    if (visible) {
      this.shown.forEach((model) => this.rebuild(model))
    }
    print(`${TAG}: overlay ${visible ? "on" : "off"}`)
  }

  /** The overlay object dies with the model root, so only state is dropped. */
  private forget(id: string): void {
    this.shown.delete(id)
    this.overlays.delete(id)
  }

  private onModelShown(model: ShownModel): void {
    this.shown.set(model.id, model)
    if (this.visible) {
      this.rebuild(model)
    }
  }

  private rebuild(model: ShownModel): void {
    const existing = this.overlays.get(model.id)
    if (existing !== undefined) {
      existing.destroy()
      this.overlays.delete(model.id)
    }

    const root = global.scene.createSceneObject(OVERLAY)
    root.setParent(model.root)
    this.overlays.set(model.id, root)

    // Rendered size in centimetres, which is what the scene is measured in.
    const w = model.shownMm.x / 10
    const h = model.shownMm.y / 10
    const d = model.shownMm.z / 10

    const box = this.buildBox(w, h, d)
    if (box !== null) {
      box.setParent(root)
    }
    this.buildLabels(root, model, w, h, d)
    root.enabled = this.visible
  }

  /**
   * The wireframe itself, as one Lines mesh.
   *
   * Corner index bits are x, y, z, so corner 5 is (max x, min y, max z).
   * The model's pivot is its bottom centre, so y runs 0 to h rather than
   * being centred like x and z.
   */
  private buildBox(w: number, h: number, d: number): SceneObject | null {
    if (this.lineMaterial === null || this.lineMaterial === undefined) {
      print(
        `${TAG}: no lineMaterial assigned, so the wireframe is skipped. ` +
          `Assign an unlit material to draw it.`
      )
      return null
    }

    const builder = new MeshBuilder([{name: "position", components: 3}])
    builder.topology = MeshTopology.Lines
    builder.indexType = MeshIndexType.UInt16

    const vertices: number[] = []
    for (let corner = 0; corner < 8; corner++) {
      vertices.push(
        corner & 1 ? w / 2 : -w / 2,
        corner & 2 ? h : 0,
        corner & 4 ? d / 2 : -d / 2
      )
    }
    builder.appendVerticesInterleaved(vertices)

    const indices: number[] = []
    for (const edge of EDGES) {
      indices.push(edge[0], edge[1])
    }
    builder.appendIndices(indices)

    if (!builder.isValid()) {
      print(`${TAG}: the wireframe mesh came out invalid, skipping it.`)
      return null
    }
    builder.updateMesh()

    const object = global.scene.createSceneObject("holocad_wireframe")
    const visual = object.createComponent("Component.RenderMeshVisual")
    visual.mesh = builder.getMesh()
    visual.clearMaterials()
    visual.addMaterial(this.lineMaterial)
    return object
  }

  private buildLabels(
    root: SceneObject,
    model: ShownModel,
    w: number,
    h: number,
    d: number
  ): void {
    const mm = model.bboxMm
    // One label per axis, placed just outside the box on that axis, plus a
    // line underneath saying what scale this is being shown at.
    this.label(root, `${mm.x.toFixed(1)} mm`,
               new vec3(0, -this.labelSizeCm, d / 2 + this.labelSizeCm))
    this.label(root, `${mm.y.toFixed(1)} mm`,
               new vec3(w / 2 + this.labelSizeCm, h / 2, d / 2))
    this.label(root, `${mm.z.toFixed(1)} mm`,
               new vec3(w / 2 + this.labelSizeCm, 0, 0))
    this.label(root, model.ratioLabel,
               new vec3(0, h + this.labelSizeCm * 1.5, 0))
  }

  private label(parent: SceneObject, text: string, position: vec3): void {
    const object = global.scene.createSceneObject("holocad_label")
    object.setParent(parent)
    object.getTransform().setLocalPosition(position)

    const label = object.createComponent("Component.Text")
    label.text = text
    label.size = Math.max(8, Math.round(this.labelSizeCm * 16))
    label.horizontalAlignment = HorizontalAlignment.Center
    label.verticalAlignment = VerticalAlignment.Center

    // Billboard, so a measurement is readable from wherever you stand.
    // LookAtMode has only LookAtPoint and LookAtDirection, so facing the
    // viewer means aiming at the camera object rather than asking for a
    // camera mode that does not exist.
    if (this.camera !== null) {
      const billboard = object.createComponent("Component.LookAtComponent")
      billboard.target = this.camera
      billboard.aimVectors = LookAtComponent.AimVectors.ZAimYUp
      billboard.lookAtMode = LookAtComponent.LookAtMode.LookAtPoint
      billboard.worldUpVector = LookAtComponent.WorldUpVector.SceneY
    }
  }

  private resolveCamera(): SceneObject | null {
    if (this.cameraObject !== null && this.cameraObject !== undefined) {
      return this.cameraObject
    }
    const count = global.scene.getRootObjectsCount()
    for (let i = 0; i < count; i++) {
      const found = DimensionOverlay.searchForCamera(global.scene.getRootObject(i))
      if (found !== null) {
        return found
      }
    }
    print(`${TAG}: no camera found, labels will not turn to face you.`)
    return null
  }

  private static searchForCamera(object: SceneObject): SceneObject | null {
    const camera = object.getComponent("Component.Camera")
    if (camera !== null && camera !== undefined) {
      return object
    }
    for (let i = 0; i < object.getChildrenCount(); i++) {
      const found = DimensionOverlay.searchForCamera(object.getChild(i))
      if (found !== null) {
        return found
      }
    }
    return null
  }
}
