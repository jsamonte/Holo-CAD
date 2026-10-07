/**
 * Connection state and the last model's details, on a Text component.
 *
 * Optional. With no Text assigned it still prints to the Logger, which is
 * enough while testing in the Lens Studio preview.
 */

import {BridgeClient, BridgeStatus} from "./BridgeClient"
import {ModelLoader, ShownModel} from "./ModelLoader"

const TAG = "HoloCAD StatusPanel"

/**
 * Shown at launch and logged at launch, because Snap's publishing checklist
 * asks for both: it is what makes a bug report from a stranger actionable.
 * Bump it with every build that goes to review.
 */
export const LENS_VERSION = "0.1.0"

@component
export class StatusPanel extends BaseScriptComponent {
  @input
  @hint("Text component to write the status into.")
  @allowUndefined
  statusText!: Text

  @input
  @hint("Object holding BridgeClient and ModelLoader. Leave empty to look on this object.")
  @allowUndefined
  sourceObject!: SceneObject

  private bridge: BridgeClient | null = null
  private loader: ModelLoader | null = null
  private connection: string = "starting"
  private pairingCode: string = ""
  private lastModel: ShownModel | null = null

  onAwake(): void {
    this.createEvent("OnStartEvent").bind(() => this.start())
  }

  private start(): void {
    print(`${TAG}: Holo-CAD lens version ${LENS_VERSION}`)
    const host = this.sourceObject ?? this.getSceneObject()
    this.bridge = host.getComponent(BridgeClient.getTypeName())
    this.loader = host.getComponent(ModelLoader.getTypeName())

    if (this.bridge === null) {
      print(`${TAG}: no BridgeClient found, connection state will not update.`)
    } else {
      this.bridge.onStatus.add((status) => this.onStatus(status))
      this.bridge.onPairingCode.add((code) => this.onPairingCode(code))
      this.onStatus(this.bridge.status)
    }

    if (this.loader === null) {
      print(`${TAG}: no ModelLoader found, model details will not update.`)
    } else {
      this.loader.onModelShown.add((shown) => this.onModelShown(shown))
    }

    this.render()
  }

  private onStatus(status: BridgeStatus): void {
    this.connection = status.state === "connected" ? "connected" : `${status.state}: ${status.detail}`
    this.render()
  }

  /**
   * The pairing code is the one thing on this panel the user must act on,
   * so it goes first and stays until a model arrives through it.
   */
  private onPairingCode(code: string): void {
    this.pairingCode = code
    this.render()
  }

  private onModelShown(shown: ShownModel): void {
    this.lastModel = shown
    this.render()
  }

  private render(): void {
    const lines: string[] = []

    if (this.pairingCode.length > 0 && this.lastModel === null) {
      lines.push(`type this code into FreeCAD: ${this.pairingCode}`)
    }
    lines.push(`bridge: ${this.connection}`)
    if (this.lastModel === null) {
      // Visible at launch, which the publishing checklist asks for. It
      // drops away once a model arrives and the panel has better things
      // to say.
      lines.push(`Holo-CAD ${LENS_VERSION}`)
    }

    if (this.lastModel !== null) {
      const m = this.lastModel
      lines.push(`model: ${m.id} v${m.version}`)
      lines.push(
        `size: ${m.shownMm.x.toFixed(1)} x ${m.shownMm.y.toFixed(1)} x ${m.shownMm.z.toFixed(1)} mm at ${m.ratioLabel}`
      )
      lines.push(`triangles: ${m.triangles}`)
      lines.push(`load: ${(m.loadSeconds * 1000).toFixed(0)} ms`)
    } else {
      lines.push("model: waiting for a push")
    }

    const text = lines.join("\n")
    if (this.statusText !== null && this.statusText !== undefined) {
      this.statusText.text = text
    }
  }
}
