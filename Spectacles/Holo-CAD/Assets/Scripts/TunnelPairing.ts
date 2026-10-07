/**
 * Lets the wearer type the tunnel words, so a published lens can find a
 * FreeCAD it could never reach directly.
 *
 * A published lens may only use `wss://`, which needs a real certificate,
 * which needs a public hostname. FreeCAD's addon gets one by running
 * cloudflared, and Cloudflare hands back something like
 *
 *     https://brighton-depth-searched-dollars.trycloudflare.com
 *
 * The suffix never changes, so only the words have to be typed, and they are
 * remembered afterwards. That matters: the hostname changes every time the
 * tunnel restarts, and retyping four words is bad enough without retyping
 * the domain too.
 *
 * Needs a TextInputModule asset in the project. Like InternetModule, its
 * absence is a build time failure with an unhelpful message, so it is worth
 * checking first when nothing happens.
 */

import {BridgeClient} from "./BridgeClient"

const TAG = "HoloCAD TunnelPairing"
const SUFFIX = ".trycloudflare.com"
const STORE_KEY = "holocad.tunnel.words"

@component
export class TunnelPairing extends BaseScriptComponent {
  @input
  @hint("Object holding the BridgeClient. Leave empty to look on this object.")
  @allowUndefined
  bridgeClientObject!: SceneObject

  @input
  @hint("Text that prompts for the words and shows what was typed.")
  @allowUndefined
  promptText!: Text

  @input
  @hint("Ask for the words as soon as the lens opens, when none are stored.")
  askAtStart: boolean = true

  @input
  @hint("Remember the words between sessions.")
  remember: boolean = true

  private bridge: BridgeClient | null = null
  private words: string = ""

  onAwake(): void {
    this.createEvent("OnStartEvent").bind(() => this.start())
  }

  private start(): void {
    const host = this.bridgeClientObject ?? this.getSceneObject()
    this.bridge = host.getComponent(BridgeClient.getTypeName())
    if (this.bridge === null) {
      print(`${TAG}: no BridgeClient found, nothing to point anywhere.`)
      return
    }

    const stored = this.load()
    if (stored.length > 0) {
      print(`${TAG}: reusing stored words "${stored}"`)
      this.apply(stored)
      return
    }
    this.show("No FreeCAD yet.\nType the words FreeCAD shows.")
    if (this.askAtStart) {
      this.ask()
    }
  }

  /** Open the keyboard. Wire this to a button to change machines later. */
  ask(): void {
    const system = global.textInputSystem
    if (system === undefined || system === null) {
      print(
        `${TAG}: no text input system. Add a TextInputModule asset to the ` +
          `project, otherwise the words cannot be typed.`
      )
      this.show("Cannot open a keyboard.\nSee the Logger.")
      return
    }

    const options = new TextInputSystem.KeyboardOptions()
    options.keyboardType = TextInputSystem.KeyboardType.Text
    options.returnKeyType = TextInputSystem.ReturnKeyType.Done
    options.enablePreview = true
    options.initialText = this.words

    let typed = this.words
    options.onTextChanged = (text: string) => {
      typed = text
      this.show(`${this.clean(text)}${SUFFIX}`)
    }
    options.onReturnKeyPressed = () => {
      system.dismissKeyboard()
      const cleaned = this.clean(typed)
      if (cleaned.length === 0) {
        this.show("Nothing typed.\nTry again.")
        return
      }
      this.save(cleaned)
      this.apply(cleaned)
    }
    options.onKeyboardStateChanged = (open: boolean) => {
      if (!open && this.words.length === 0) {
        this.show("No FreeCAD yet.\nType the words FreeCAD shows.")
      }
    }

    print(`${TAG}: asking for the tunnel words`)
    system.requestKeyboard(options)
  }

  /**
   * Tidy what was typed into something that can be a hostname.
   *
   * People paste the whole url, or type the suffix anyway, or add spaces
   * between the words because that is what the prompt looks like. All three
   * are easy to accept and annoying to be refused for.
   */
  private clean(text: string): string {
    let out = (text ?? "").trim().toLowerCase()
    out = out.replace("https://", "").replace("http://", "")
    out = out.replace("wss://", "").replace("ws://", "")
    const slash = out.indexOf("/")
    if (slash >= 0) {
      out = out.substring(0, slash)
    }
    if (out.indexOf(SUFFIX) >= 0) {
      out = out.substring(0, out.indexOf(SUFFIX))
    }
    // Spaces where hyphens belong, and nothing a hostname cannot hold.
    out = out.split(" ").join("-")
    let kept = ""
    for (let i = 0; i < out.length; i++) {
      const c = out.charAt(i)
      if ((c >= "a" && c <= "z") || (c >= "0" && c <= "9") || c === "-") {
        kept += c
      }
    }
    while (kept.length > 0 && kept.charAt(0) === "-") {
      kept = kept.substring(1)
    }
    while (kept.length > 0 && kept.charAt(kept.length - 1) === "-") {
      kept = kept.substring(0, kept.length - 1)
    }
    return kept
  }

  private apply(words: string): void {
    this.words = words
    const url = `wss://${words}${SUFFIX}/ws`
    print(`${TAG}: connecting to ${url}`)
    this.show(`${words}${SUFFIX}\nconnecting`)
    if (this.bridge !== null) {
      this.bridge.bridgeUrl = url
      this.bridge.reconnect()
    }
  }

  /** Forget the stored words and ask again. Wire this to a button. */
  forget(): void {
    this.words = ""
    if (this.remember) {
      try {
        global.persistentStorageSystem.store.remove(STORE_KEY)
      } catch (e) {
        print(`${TAG}: could not clear the stored words: ${e}`)
      }
    }
    this.ask()
  }

  private load(): string {
    if (!this.remember) {
      return ""
    }
    try {
      const stored = global.persistentStorageSystem.store.getString(STORE_KEY)
      return this.clean(stored ?? "")
    } catch (e) {
      // A first run has nothing stored, which is not worth reporting.
      return ""
    }
  }

  private save(words: string): void {
    if (!this.remember) {
      return
    }
    try {
      global.persistentStorageSystem.store.putString(STORE_KEY, words)
    } catch (e) {
      print(`${TAG}: could not remember the words: ${e}`)
    }
  }

  private show(text: string): void {
    if (this.promptText !== null && this.promptText !== undefined) {
      this.promptText.text = text
    }
  }
}
