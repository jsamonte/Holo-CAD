/**
 * A row of tappable buttons for the things that had no way to be undone.
 *
 * Before this, a part dragged behind you stayed behind you, a model pinched
 * down to a speck stayed a speck, and the only reset was tapping the one
 * size label under one model. The three reset buttons here act on
 * everything at once, and the fourth decides whether a grab moves one part
 * or the whole assembly.
 *
 * Put this on an object that also has a FloatingPanel, which is what makes
 * it sit in the world, face you and move when you drag it, exactly like the
 * pairing panel. This script only builds the buttons and wires them.
 *
 * The buttons are Text plus a box collider plus an Interactable, which is
 * the same thing the size label under a model does. There is no UIKit
 * dependency on purpose: this has to work in a project where only SIK is
 * installed.
 *
 * Written without optional chaining and without a module level type alias.
 * With either of those in the file, Lens Studio registered no @input at
 * all: the component came up with an empty input list and nothing could be
 * wired to it, while the file itself compiled without complaint.
 */

import {Interactable} from "SpectaclesInteractionKit.lspkg/Components/Interaction/Interactable/Interactable"

import {ModelLoader} from "./ModelLoader"
import {ModelPlacement} from "./ModelPlacement"

const TAG = "HoloCAD ControlPanel"
const GRAB_WHOLE_LABEL = "Grab: whole"
const GRAB_PARTS_LABEL = "Grab: parts"

@component
export class ControlPanel extends BaseScriptComponent {
  @input
  @hint("Object holding ModelPlacement. Leave empty to search the scene.")
  @allowUndefined
  placementObject!: SceneObject

  @input
  @hint("Object holding ModelLoader. Leave empty to search the scene.")
  @allowUndefined
  loaderObject!: SceneObject

  @input
  @hint("Optional heading above the buttons. Leave blank for none.")
  title: string = "Holo-CAD"

  @input
  @hint("Height of one button, in cm. Also sets the text size.")
  buttonHeightCm: number = 4

  @input
  @hint("Width of one button, in cm.")
  buttonWidthCm: number = 20

  @input
  @hint("Gap between buttons, in cm.")
  spacingCm: number = 1.5

  @input
  @hint("Stack the buttons vertically rather than in a row.")
  vertical: boolean = true

  private placement: ModelPlacement | null = null
  private loader: ModelLoader | null = null
  private grabLabel: Text | null = null

  onAwake(): void {
    this.createEvent("OnStartEvent").bind(() => this.start())
  }

  private start(): void {
    this.placement = this.findPlacement()
    this.loader = this.findLoader()
    if (this.placement === null) {
      print(TAG + ": no ModelPlacement found, the buttons would do nothing.")
      return
    }

    this.build()

    // Keep the grab button honest if something else flips the mode.
    this.placement.onGrabModeChanged.add(() => this.refresh())
    print(TAG + ": ready")
  }

  private build(): void {
    const panel = this.getSceneObject()
    const step = this.buttonHeightCm + this.spacingCm
    const columnStep = this.buttonWidthCm + this.spacingCm
    const stride = this.vertical ? step : columnStep

    // Four buttons, centred on the panel.
    const offset = (3 * stride) / 2

    if (this.title.length > 0) {
      const above = this.vertical
        ? new vec3(0, offset + step, 0)
        : new vec3(0, step, 0)
      this.text(panel, this.title, above, this.buttonHeightCm * 0.5)
    }

    this.addButton(panel, "Reset size", 0, offset, stride, () => {
      if (this.placement !== null) {
        this.placement.resetSizeAll()
      }
    })
    this.addButton(panel, "Bring to me", 1, offset, stride, () => {
      if (this.placement !== null) {
        this.placement.resetPosition()
      }
    })
    this.addButton(panel, "Reset all", 2, offset, stride, () => {
      if (this.placement !== null) {
        this.placement.resetAll()
      }
    })
    this.grabLabel = this.addButton(
      panel,
      this.grabCaption(),
      3,
      offset,
      stride,
      () => {
        if (this.placement !== null) {
          this.placement.toggleGrabWhole()
        }
      }
    )
  }

  private addButton(
    panel: SceneObject,
    caption: string,
    index: number,
    offset: number,
    stride: number,
    tapped: () => void
  ): Text | null {
    const at = this.vertical
      ? new vec3(0, offset - index * stride, 0)
      : new vec3(-offset + index * stride, 0, 0)
    return this.button(panel, caption, at, tapped)
  }

  /** What the grab button should say right now. */
  private grabCaption(): string {
    if (this.placement !== null && this.placement.grabbingWhole()) {
      return GRAB_WHOLE_LABEL
    }
    return GRAB_PARTS_LABEL
  }

  /** One tappable button: a caption, a box to hit, and what it does. */
  private button(
    parent: SceneObject,
    caption: string,
    at: vec3,
    tapped: () => void
  ): Text | null {
    const object = global.scene.createSceneObject("holocad_button")
    object.setParent(parent)
    object.getTransform().setLocalPosition(at)

    const label = object.createComponent("Component.Text")
    label.text = caption
    label.size = Math.max(8, Math.round(this.buttonHeightCm * 7))
    label.horizontalAlignment = HorizontalAlignment.Center
    label.verticalAlignment = VerticalAlignment.Center

    // A Text has no collider, so the hit area is stated rather than
    // measured. Depth is deliberately generous: a flat plate is easy to
    // miss with a pinch that lands slightly in front of or behind it.
    const collider = object.createComponent("Physics.ColliderComponent")
    collider.debugDrawEnabled = false
    const box = Shape.createBoxShape()
    box.size = new vec3(this.buttonWidthCm, this.buttonHeightCm, 2)
    collider.shape = box

    const interactable = object.createComponent(Interactable.getTypeName())
    interactable.onTriggerEnd.add(() => {
      tapped()
      this.refresh()
    })
    return label
  }

  /** Plain text with no hit area, for the heading. */
  private text(parent: SceneObject, body: string, at: vec3, sizeCm: number): void {
    const object = global.scene.createSceneObject("holocad_panel_title")
    object.setParent(parent)
    object.getTransform().setLocalPosition(at)
    const label = object.createComponent("Component.Text")
    label.text = body
    label.size = Math.max(8, Math.round(sizeCm * 7))
    label.horizontalAlignment = HorizontalAlignment.Center
    label.verticalAlignment = VerticalAlignment.Center
  }

  /** Re-read the one caption that shows a state, after anything changes it. */
  private refresh(): void {
    if (this.grabLabel !== null) {
      this.grabLabel.text = this.grabCaption()
    }
  }

  private findPlacement(): ModelPlacement | null {
    if (this.placementObject !== null && this.placementObject !== undefined) {
      const found = this.placementObject.getComponent(ModelPlacement.getTypeName())
      if (found !== null && found !== undefined) {
        return found
      }
    }
    return ControlPanel.search(ModelPlacement.getTypeName())
  }

  private findLoader(): ModelLoader | null {
    if (this.loaderObject !== null && this.loaderObject !== undefined) {
      const found = this.loaderObject.getComponent(ModelLoader.getTypeName())
      if (found !== null && found !== undefined) {
        return found
      }
    }
    return ControlPanel.search(ModelLoader.getTypeName())
  }

  /**
   * Find a component anywhere in the scene.
   *
   * The panel is a separate object from the one carrying the logic, and
   * asking people to wire two references by hand is how a panel ends up
   * with dead buttons. The inspector inputs still win when they are set.
   *
   * typeName is deliberately any: getTypeName() hands back a TypeName<T>,
   * not a string, and typing it as string fails to compile against both
   * this signature and getComponent's overloads.
   */
  private static search(typeName: any): any {
    const count = global.scene.getRootObjectsCount()
    for (let i = 0; i < count; i++) {
      const found = ControlPanel.searchFrom(global.scene.getRootObject(i), typeName)
      if (found !== null) {
        return found
      }
    }
    return null
  }

  private static searchFrom(object: SceneObject, typeName: any): any {
    const here = object.getComponent(typeName)
    if (here !== null && here !== undefined) {
      return here
    }
    for (let i = 0; i < object.getChildrenCount(); i++) {
      const found = ControlPanel.searchFrom(object.getChild(i), typeName)
      if (found !== null) {
        return found
      }
    }
    return null
  }
}
