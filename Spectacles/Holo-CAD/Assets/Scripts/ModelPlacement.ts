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

import {ModelLoader, ShownModel} from "./ModelLoader"

const TAG = "HoloCAD ModelPlacement"
const GRAB_BOX = "holocad_grab_box"

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
  @hint("Allow pinch to resize. Off keeps the model at the size FreeCAD says.")
  freeScale: boolean = false

  @input
  @hint("Padding added to the grab box, in cm, so thin parts stay catchable.")
  grabPaddingCm: number = 2

  private loader: ModelLoader | null = null
  private wired: Map<string, InteractableManipulation> = new Map()

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
    this.loader.onModelRemoved.add((id) => this.wired.delete(id))
    print(`${TAG}: ready`)
  }

  private onModelShown(shown: ShownModel): void {
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
    manipulation.setCanScale(this.freeScale)

    this.wired.set(shown.id, manipulation)
    print(
      `${TAG}: ${shown.id} can be grabbed` +
        (this.freeScale ? " and resized" : ", resizing is off")
    )
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

  /** Turn pinch to resize on or off at runtime. */
  setFreeScale(enabled: boolean): void {
    this.freeScale = enabled
    this.wired.forEach((manipulation) => manipulation.setCanScale(enabled))
    print(`${TAG}: free scale ${enabled ? "on" : "off"}`)
  }
}
