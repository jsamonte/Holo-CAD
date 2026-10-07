/**
 * Makes a loaded model grabbable, and owns where it sits.
 *
 * The placement lives on the per id root, which survives every update, so
 * moving a part and then editing it in FreeCAD leaves the part exactly where
 * you put it. That is the difference between a live model and one that jumps
 * back to the spawn point on every recompute.
 *
 * Scaling by hand is off by default on purpose. The whole point of the
 * project is that the model is the size FreeCAD says it is, so pinch to
 * zoom would quietly undo it. Turn on freeScale when you want to break that
 * rule deliberately.
 *
 * Needs the Spectacles Interaction Kit, which the Spectacles template ships.
 */

import {Interactable} from "SpectaclesInteractionKit.lspkg/Components/Interaction/Interactable/Interactable"
import {InteractableManipulation} from "SpectaclesInteractionKit.lspkg/Components/Interaction/InteractableManipulation/InteractableManipulation"

import {Signal} from "./BridgeClient"
import {ModelLoader, ShownModel} from "./ModelLoader"

const TAG = "HoloCAD ModelPlacement"
const GRAB_BOX = "holocad_grab_box"

/** How big a model is right now, relative to the size FreeCAD reported. */
export type UserScale = {
  id: string
  /** 1 means true size. 2 means twice as big as the real part. */
  factor: number
  /** Size as currently rendered, in millimetres. */
  shownMm: vec3
}

@component
export class ModelPlacement extends BaseScriptComponent {
  @input
  @hint("Object holding the ModelLoader. Leave empty to look on this object.")
  @allowUndefined
  modelLoaderObject!: SceneObject

  @input
  @hint("Let the model be grabbed, moved and turned.")
  allowManipulation: boolean = true

  @input
  @hint("Two handed pinch to resize. Reset size puts it back to 1:1.")
  allowResize: boolean = true

  @input
  @hint("Padding added to the grab box, in cm, so thin parts stay catchable.")
  grabPaddingCm: number = 2

  @input
  @hint("Smallest you can shrink to, as a fraction of true size.")
  minScale: number = 0.05

  @input
  @hint("Largest you can grow to, as a multiple of true size.")
  maxScale: number = 20

  /**
   * Fired while a model is resized by hand, with its id and the multiple of
   * true size it is now at. 1 means it is at the size FreeCAD says.
   */
  readonly onUserScaleChanged = new Signal<UserScale>()

  private loader: ModelLoader | null = null
  private wired: Map<string, InteractableManipulation> = new Map()
  private shown: Map<string, ShownModel> = new Map()

  onAwake(): void {
    this.createEvent("OnStartEvent").bind(() => this.start())
  }

  private start(): void {
    const host = this.modelLoaderObject ?? this.getSceneObject()
    this.loader = host.getComponent(ModelLoader.getTypeName())
    if (this.loader === null) {
      print(`${TAG}: no ModelLoader found, nothing to place.`)
      return
    }
    this.loader.onModelShown.add((shown) => this.onModelShown(shown))
    this.loader.onModelRemoved.add((id) => {
      this.wired.delete(id)
      this.shown.delete(id)
    })
    print(`${TAG}: ready`)
  }

  private onModelShown(shown: ShownModel): void {
    this.shown.set(shown.id, shown)
    if (!this.allowManipulation) {
      return
    }
    const existing = this.wired.get(shown.id)
    if (existing !== undefined) {
      // Already grabbable. Only the collider needs to follow the new size.
      this.fitCollider(shown)
      return
    }
    this.makeGrabbable(shown)
  }

  private makeGrabbable(shown: ShownModel): void {
    const root = shown.root

    // An Interactable is only targetable if something has a collider, and the
    // model's own meshes are rebuilt on every update, so the collider lives
    // on the root and is resized instead.
    this.fitCollider(shown)

    const interactable = root.createComponent(Interactable.getTypeName())
    const manipulation = root.createComponent(InteractableManipulation.getTypeName())
    manipulation.setCanTranslate(true)
    manipulation.setCanRotate(true)
    manipulation.setCanScale(this.allowResize)
    manipulation.minimumScaleFactor = this.minScale
    manipulation.maximumScaleFactor = this.maxScale

    // Report the size while it is being changed, not only at the end, so
    // the label keeps up with the hands.
    manipulation.onScaleUpdate.add(() => this.reportScale(shown.id))
    manipulation.onScaleEnd.add(() => this.reportScale(shown.id))

    this.wired.set(shown.id, manipulation)
    print(
      `${TAG}: ${shown.id} can be grabbed and turned` +
        (this.allowResize ? ", and resized with two hands" : "")
    )
  }

  /**
   * Work out how big the model is now and tell anyone listening.
   *
   * The root carries only what the hands have done to it: ModelLoader puts
   * the true size correction on the wrapper underneath. So the root's own
   * scale is exactly the multiple of true size, and 1 means the part is the
   * size FreeCAD says it is.
   */
  private reportScale(id: string): void {
    const shown = this.shown.get(id)
    if (shown === undefined) {
      return
    }
    const factor = shown.root.getTransform().getLocalScale().x
    this.onUserScaleChanged.emit({
      id: id,
      factor: factor,
      shownMm: shown.shownMm.uniformScale(factor)
    })
  }

  /** Put a model back to the size FreeCAD says it is. */
  resetSize(id?: string): void {
    const ids = id !== undefined ? [id] : Array.from(this.shown.keys())
    for (const each of ids) {
      const shown = this.shown.get(each)
      if (shown === undefined) {
        continue
      }
      shown.root.getTransform().setLocalScale(new vec3(1, 1, 1))
      this.reportScale(each)
      print(`${TAG}: ${each} back to true size`)
    }
  }

  /** How many times true size a model is currently drawn at. */
  userScale(id: string): number {
    const shown = this.shown.get(id)
    if (shown === undefined) {
      return 1
    }
    return shown.root.getTransform().getLocalScale().x
  }

  /**
   * Size the grab box to the model as it is actually rendered.
   *
   * The collider sits on a child rather than the root, for two reasons. The
   * model's pivot is its bottom centre, so a box centred on the root would
   * hang half below the part. And SIK searches an Interactable's descendants
   * for colliders, so a child is found just as well as the root itself.
   */
  private fitCollider(shown: ShownModel): void {
    const root = shown.root
    let holder: SceneObject | null = null
    const children = root.getChildrenCount()
    for (let i = 0; i < children; i++) {
      const child = root.getChild(i)
      if (child.name === GRAB_BOX) {
        holder = child
        break
      }
    }
    if (holder === null) {
      holder = global.scene.createSceneObject(GRAB_BOX)
      holder.setParent(root)
    }

    // shownMm is the rendered size in millimetres, Lens Studio works in cm.
    const padding = Math.max(0, this.grabPaddingCm)
    const halfHeightCm = shown.shownMm.y / 20
    holder.getTransform().setLocalPosition(new vec3(0, halfHeightCm, 0))

    let collider = holder.getComponent("Physics.ColliderComponent")
    if (collider === null || collider === undefined) {
      collider = holder.createComponent("Physics.ColliderComponent")
      collider.debugDrawEnabled = false
      // Nothing should fall, bounce or push the model about. The collider is
      // only here so the interaction system has something to hit.
      collider.intangible = true
    }
    const box = Shape.createBoxShape()
    box.size = new vec3(
      shown.shownMm.x / 10 + padding,
      shown.shownMm.y / 10 + padding,
      shown.shownMm.z / 10 + padding
    )
    collider.shape = box
  }

  /** Put every model back in front of the user. Wire this to a button. */
  rePlace(): void {
    if (this.loader === null) {
      return
    }
    this.loader.replaceAll()
    print(`${TAG}: models re-placed in front of you`)
  }

  /** Turn two handed resizing on or off at runtime. */
  setAllowResize(enabled: boolean): void {
    this.allowResize = enabled
    this.wired.forEach((manipulation) => manipulation.setCanScale(enabled))
    print(`${TAG}: resizing ${enabled ? "on" : "off"}`)
  }
}
