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
const ASSEMBLY_BOX = "holocad_assembly_box"

/** Said of a model when a hand comes within reach of it, or leaves. */
export type Proximity = {
  /** The model's id, or ASSEMBLY_TARGET for the assembly as a whole. */
  id: string
  near: boolean
}

/** Stands for every model at once, used when the assembly is the target. */
export const ASSEMBLY_TARGET = "*"

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
  @hint("Start out grabbing the whole assembly as one piece rather than single parts.")
  grabWhole: boolean = false

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

  /** Fired when the grab target changes, with true for the whole assembly. */
  readonly onGrabModeChanged = new Signal<boolean>()

  /**
   * Fired when a hand comes within reach of a model, and when it leaves.
   *
   * This is SIK's own hover, which is what decides whether a pinch would
   * land on the model, so it is the honest answer to "is my hand near this
   * part" rather than a distance guessed at here.
   */
  readonly onProximityChanged = new Signal<Proximity>()

  private loader: ModelLoader | null = null
  private wired: Map<string, InteractableManipulation> = new Map()
  private parts: Map<string, Interactable> = new Map()
  private shown: Map<string, ShownModel> = new Map()
  private assemblyGrab: InteractableManipulation | null = null
  private assemblyInteractable: Interactable | null = null

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
      this.parts.delete(id)
      this.shown.delete(id)
      this.fitAssemblyCollider()
    })
    this.applyGrabMode()
    print(`${TAG}: ready, grabbing ${this.grabWhole ? "the whole assembly" : "single parts"}`)
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
    } else {
      this.makeGrabbable(shown)
    }
    this.fitAssemblyCollider()
    this.applyGrabMode()
  }

  private makeGrabbable(shown: ShownModel): void {
    const root = shown.root

    // An Interactable is only targetable if something has a collider, and the
    // model's own meshes are rebuilt on every update, so the collider lives
    // on the root and is resized instead.
    this.fitCollider(shown)

    const interactable = root.createComponent(Interactable.getTypeName())
    interactable.onHoverEnter.add(() =>
      this.onProximityChanged.emit({id: shown.id, near: true}))
    interactable.onHoverExit.add(() =>
      this.onProximityChanged.emit({id: shown.id, near: false}))
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
    this.parts.set(shown.id, interactable)
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
      // Not intangible: an intangible collider is skipped by the
      // interaction raycast, so the model looked grabbable and was not.
      // With no rigid body on it this is inert anyway.
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
    if (this.assemblyGrab !== null) {
      this.assemblyGrab.setCanScale(enabled)
    }
    print(`${TAG}: resizing ${enabled ? "on" : "off"}`)
  }

  // ---- grabbing the whole assembly ---------------------------------------

  /**
   * Choose between dragging single parts and dragging the lot.
   *
   * Only one of the two is ever live. Nesting a grabbable assembly around
   * grabbable parts means a pinch is ambiguous, and SIK resolves that by
   * picking whichever collider the ray hits first, which from most angles
   * is the part. You would reach for the assembly and move one bracket.
   * So the unused side is switched off rather than left to compete.
   */
  setGrabWhole(whole: boolean): void {
    this.grabWhole = whole
    this.applyGrabMode()
    print(`${TAG}: grabbing ${whole ? "the whole assembly" : "single parts"}`)
    this.onGrabModeChanged.emit(whole)
  }

  /** Flip between whole assembly and single parts. Wire this to a button. */
  toggleGrabWhole(): void {
    this.setGrabWhole(!this.grabWhole)
  }

  /** True when a grab moves the whole assembly. */
  grabbingWhole(): boolean {
    return this.grabWhole
  }

  private applyGrabMode(): void {
    if (!this.allowManipulation) {
      return
    }
    if (this.grabWhole) {
      this.ensureAssemblyGrab()
    }
    const parts = !this.grabWhole
    this.parts.forEach((interactable) => {
      interactable.enabled = parts
    })
    if (this.assemblyInteractable !== null) {
      this.assemblyInteractable.enabled = this.grabWhole
    }
  }

  private ensureAssemblyGrab(): void {
    if (this.assemblyGrab !== null || this.loader === null) {
      return
    }
    const root = this.loader.assemblyRoot()
    this.fitAssemblyCollider()
    this.assemblyInteractable = root.createComponent(Interactable.getTypeName())
    this.assemblyInteractable.onHoverEnter.add(() =>
      this.onProximityChanged.emit({id: ASSEMBLY_TARGET, near: true}))
    this.assemblyInteractable.onHoverExit.add(() =>
      this.onProximityChanged.emit({id: ASSEMBLY_TARGET, near: false}))
    const manipulation = root.createComponent(InteractableManipulation.getTypeName())
    manipulation.setCanTranslate(true)
    manipulation.setCanRotate(true)
    manipulation.setCanScale(this.allowResize)
    manipulation.minimumScaleFactor = this.minScale
    manipulation.maximumScaleFactor = this.maxScale
    this.assemblyGrab = manipulation
  }

  /**
   * Size one box around every model, in assembly space.
   *
   * Rotation of the individual parts is ignored and made up for with
   * padding. A tight box would mean recomputing an oriented hull every time
   * a part is turned, for a grab target that only has to be easy to reach.
   */
  private fitAssemblyCollider(): void {
    if (this.loader === null || this.shown.size === 0) {
      return
    }
    const root = this.loader.assemblyRoot()
    let minX = Infinity, minY = Infinity, minZ = Infinity
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity

    this.shown.forEach((shown) => {
      const transform = shown.root.getTransform()
      const at = transform.getLocalPosition()
      const factor = transform.getLocalScale().x
      // shownMm is the rendered size in mm, Lens Studio works in cm, and
      // the model sits on its root rather than being centred on it.
      const halfX = (shown.shownMm.x * factor) / 20
      const halfZ = (shown.shownMm.z * factor) / 20
      const height = (shown.shownMm.y * factor) / 10
      minX = Math.min(minX, at.x - halfX)
      maxX = Math.max(maxX, at.x + halfX)
      minY = Math.min(minY, at.y)
      maxY = Math.max(maxY, at.y + height)
      minZ = Math.min(minZ, at.z - halfZ)
      maxZ = Math.max(maxZ, at.z + halfZ)
    })
    if (!isFinite(minX) || !isFinite(maxX)) {
      return
    }

    let holder = ModelPlacement.childNamed(root, ASSEMBLY_BOX)
    if (holder === null) {
      holder = global.scene.createSceneObject(ASSEMBLY_BOX)
      holder.setParent(root)
    }
    holder.getTransform().setLocalPosition(
      new vec3((minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2)
    )

    let collider = holder.getComponent("Physics.ColliderComponent")
    if (collider === null || collider === undefined) {
      collider = holder.createComponent("Physics.ColliderComponent")
      collider.debugDrawEnabled = false
    }
    const padding = Math.max(0, this.grabPaddingCm)
    const box = Shape.createBoxShape()
    box.size = new vec3(
      maxX - minX + padding,
      maxY - minY + padding,
      maxZ - minZ + padding
    )
    collider.shape = box
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

  // ---- putting everything back -------------------------------------------

  /**
   * Undo everything the hands have done: size, position and rotation, for
   * every part and for the assembly.
   *
   * This is the way out of having dragged a part somewhere behind you, or
   * shrunk the assembly to a speck, neither of which was recoverable
   * before.
   */
  resetAll(): void {
    if (this.loader === null) {
      return
    }
    // Parts first, so the assembly is placed around models that are back
    // at true size rather than around whatever they had been stretched to.
    this.shown.forEach((shown) => {
      const transform = shown.root.getTransform()
      transform.setLocalRotation(quat.quatIdentity())
      transform.setLocalScale(new vec3(1, 1, 1))
    })
    // Back to where the addon said each part belongs, which is what makes
    // the assembly the right shape again after parts have been dragged.
    this.loader.restoreOffsets()
    this.loader.resetAssembly()
    this.loader.replaceAll()
    this.shown.forEach((_shown, id) => this.reportScale(id))
    this.fitAssemblyCollider()
    print(`${TAG}: size, rotation and position reset on everything`)
  }

  /**
   * Bring everything back within reach, without touching its size.
   *
   * The assembly's own drag is undone first, otherwise re-placing the parts
   * inside an assembly that has itself been pushed away would land them
   * somewhere else again.
   */
  resetPosition(): void {
    if (this.loader === null) {
      return
    }
    this.loader.resetAssemblyPlacement()
    this.shown.forEach((shown) => {
      shown.root.getTransform().setLocalRotation(quat.quatIdentity())
    })
    this.loader.restoreOffsets()
    this.loader.replaceAll()
    this.fitAssemblyCollider()
    print(`${TAG}: everything brought back in front of you`)
  }

  /** Put every part back to true size, assembly included. */
  resetSizeAll(): void {
    if (this.loader !== null) {
      const transform = this.loader.assemblyRoot().getTransform()
      transform.setLocalScale(new vec3(1, 1, 1))
    }
    this.resetSize()
    this.fitAssemblyCollider()
  }
}
