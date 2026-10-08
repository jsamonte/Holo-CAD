/**
 * A text panel that sits in the world and can be grabbed and moved.
 *
 * The status and pairing text used to be parented to the camera, which
 * meant it followed your head and sat in the middle of whatever you were
 * looking at. Fine for a message you read once, bad for one that stays up
 * while you work on a part.
 *
 * So this places itself in front of you once, then stays where it is put,
 * and can be moved like anything else: grab it and drag.
 *
 * Put this on an object with Text components under it. It sizes its own
 * collider from the panel size inputs rather than measuring the text,
 * because the text changes constantly and a grab box that resized under
 * your fingers would be worse than one slightly the wrong size.
 */

import {Interactable} from "SpectaclesInteractionKit.lspkg/Components/Interaction/Interactable/Interactable"
import {InteractableManipulation} from "SpectaclesInteractionKit.lspkg/Components/Interaction/InteractableManipulation/InteractableManipulation"

const TAG = "HoloCAD FloatingPanel"

@component
export class FloatingPanel extends BaseScriptComponent {
  @input
  @hint("How far in front of you the panel starts, in cm.")
  distanceCm: number = 70

  @input
  @hint("How far below eye level it starts, in cm. Negative puts it above.")
  dropCm: number = 18

  @input
  @hint("Width of the grab area, in cm.")
  widthCm: number = 46

  @input
  @hint("Height of the grab area, in cm.")
  heightCm: number = 22

  @input
  @hint("Let the panel be grabbed and moved.")
  grabbable: boolean = true

  @input
  @hint("Keep the panel turned towards you as you move around it.")
  faceUser: boolean = true

  @input
  @hint("Camera to place in front of. Leave empty to find the main camera.")
  @allowUndefined
  cameraObject!: SceneObject

  private camera: SceneObject | null = null

  onAwake(): void {
    this.createEvent("OnStartEvent").bind(() => this.start())
  }

  private start(): void {
    this.camera = this.resolveCamera()
    this.placeInFront()
    if (this.grabbable) {
      this.makeGrabbable()
    }
    if (this.faceUser && this.camera !== null) {
      const billboard = this.getSceneObject().createComponent(
        "Component.LookAtComponent"
      )
      billboard.target = this.camera
      billboard.aimVectors = LookAtComponent.AimVectors.ZAimYUp
      billboard.lookAtMode = LookAtComponent.LookAtMode.LookAtPoint
      billboard.worldUpVector = LookAtComponent.WorldUpVector.SceneY
    }
    print(`${TAG}: ready`)
  }

  /** Put the panel where it can be read, once, at start. */
  placeInFront(): void {
    if (this.camera === null) {
      print(`${TAG}: no camera found, leaving the panel where it is.`)
      return
    }
    const cameraTransform = this.camera.getTransform()
    const eye = cameraTransform.getWorldPosition()
    // In Lens Studio the direction a camera looks is its back vector.
    const viewDirection = cameraTransform.back.normalize()
    const position = eye
      .add(viewDirection.uniformScale(this.distanceCm))
      .add(new vec3(0, -this.dropCm, 0))
    this.getSceneObject().getTransform().setWorldPosition(position)
  }

  private makeGrabbable(): void {
    const object = this.getSceneObject()

    // The panel is text, which has no collider, so the grab area is stated
    // rather than measured.
    let collider = object.getComponent("Physics.ColliderComponent")
    if (collider === null || collider === undefined) {
      collider = object.createComponent("Physics.ColliderComponent")
      collider.debugDrawEnabled = false
    }
    const box = Shape.createBoxShape()
    box.size = new vec3(this.widthCm, this.heightCm, 2)
    collider.shape = box

    object.createComponent(Interactable.getTypeName())
    const manipulation = object.createComponent(
      InteractableManipulation.getTypeName()
    )
    manipulation.setCanTranslate(true)
    // Rotation and scale stay off: a panel that can be turned edge on or
    // shrunk to nothing is a panel you cannot read, and the billboard
    // already keeps it facing you.
    manipulation.setCanRotate(false)
    manipulation.setCanScale(false)
  }

  private resolveCamera(): SceneObject | null {
    if (this.cameraObject !== null && this.cameraObject !== undefined) {
      return this.cameraObject
    }
    const count = global.scene.getRootObjectsCount()
    for (let i = 0; i < count; i++) {
      const found = FloatingPanel.searchForCamera(global.scene.getRootObject(i))
      if (found !== null) {
        return found
      }
    }
    return null
  }

  private static searchForCamera(object: SceneObject): SceneObject | null {
    const camera = object.getComponent("Component.Camera")
    if (camera !== null && camera !== undefined) {
      return object
    }
    for (let i = 0; i < object.getChildrenCount(); i++) {
      const found = FloatingPanel.searchForCamera(object.getChild(i))
      if (found !== null) {
        return found
      }
    }
    return null
  }
}
