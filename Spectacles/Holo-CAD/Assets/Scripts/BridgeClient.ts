/**
 * WebSocket client for the Holo-CAD bridge running on the laptop.
 *
 * Deals only in messages: it connects, parses, and hands decoded updates to
 * listeners. It knows nothing about the scene, so ModelLoader owns everything
 * visual.
 *
 * The socket drops whenever the glasses sleep or Wi-Fi hiccups, so this
 * reconnects on its own with a doubling backoff and never gives up.
 *
 * The bridge prints the exact url to paste into bridgeUrl when it starts.
 */

export type BboxMm = {
  x: number
  y: number
  z: number
}

export type ScaleMode = "true_size" | "ratio" | "fit"

export type ScaleSpec = {
  mode: ScaleMode
  factor: number
  target_mm: number | null
}

export type ModelUpdate = {
  type: "model_update"
  id: string
  version: number
  url: string
  bbox_mm: BboxMm
  scale: ScaleSpec
  triangles: number
  /**
   * One [r, g, b, a] per mesh, in the order the meshes appear in the GLB.
   *
   * The GLB already carries these as baseColorFactor, but Lens Studio
   * instantiates glTF against a single template material and the file's own
   * colours did not survive that, so the lens tints the meshes from this
   * list instead. Empty means leave the template material alone.
   */
  colours: number[][]
  /**
   * Where this part sits inside the send, in the lens's own axes and in
   * millimetres, relative to the whole send's bottom centre.
   *
   * The addon works this out, because only it knows the other parts. Each
   * part's mesh is written about its own origin, so without this every part
   * of an assembly would arrive stacked on the same spot.
   */
  offset_mm: vec3 | null
  /**
   * Lens Studio units per unit in the file, or 0 when the addon cannot say.
   *
   * The lens used to derive this by measuring the mesh it had loaded. That
   * measurement is unreliable: across a real 21 part assembly it came out
   * between two and five times too large for most parts, so each was scaled
   * by a wrong factor of its own.
   */
  cm_per_unit: number
  pushed_ms?: number
  bytes?: number
  /** Seconds on the lens clock when this message arrived. */
  receivedAt: number
}

export type BridgeState = "idle" | "connecting" | "connected" | "retrying" | "failed"

export type BridgeStatus = {
  state: BridgeState
  detail: string
}

/**
 * Minimal listener list. SIK ships a richer Event class but the lens should
 * work before any package is installed, so this stays self contained.
 */
export class Signal<T> {
  private handlers: ((value: T) => void)[] = []

  add(handler: (value: T) => void): void {
    this.handlers.push(handler)
  }

  remove(handler: (value: T) => void): void {
    const i = this.handlers.indexOf(handler)
    if (i >= 0) {
      this.handlers.splice(i, 1)
    }
  }

  emit(value: T): void {
    for (const handler of this.handlers.slice()) {
      handler(value)
    }
  }
}

const TAG = "HoloCAD BridgeClient"

@component
export class BridgeClient extends BaseScriptComponent {
  @input
  @hint("ws://<laptop LAN ip>:8765/ws  The bridge prints this when it starts. Not 127.0.0.1.")
  bridgeUrl: string = "ws://192.168.1.50:8765/ws"

  @input
  @hint("Required. Drop the project Internet Module asset here. The require fallback does not resolve.")
  @allowUndefined
  internetModule!: InternetModule

  @input
  @hint("Print every message to the Logger.")
  verbose: boolean = false

  @input
  @hint("Seconds between keepalive pings. 0 turns them off.")
  pingSeconds: number = 15

  /** Fired for every model_update the bridge sends. */
  readonly onModelUpdate = new Signal<ModelUpdate>()

  /** Fired when FreeCAD says a model is gone, carrying its id. */
  readonly onModelRemove = new Signal<string>()

  /**
   * Fired with the pairing code when connected through a relay.
   *
   * A relay cannot know which FreeCAD belongs to which pair of glasses, so
   * it issues a code, the lens shows it, and the user types it into
   * FreeCAD. Typing happens on the laptop rather than on the glasses,
   * which is the only reason this is bearable.
   */
  readonly onPairingCode = new Signal<string>()

  /** Fired on every connection state change. */
  readonly onStatus = new Signal<BridgeStatus>()

  private internet: InternetModule | null = null
  private socket: WebSocket | null = null
  private pairingCode: string = ""
  private held: boolean = false
  private closedByUs = false
  private retryDelay = 1
  private pingAccumulator = 0

  private static readonly MAX_RETRY = 15

  private statusValue: BridgeStatus = {state: "idle", detail: "not started"}

  get status(): BridgeStatus {
    return this.statusValue
  }

  get isOpen(): boolean {
    return this.socket !== null && this.socket.readyState === 1
  }

  onAwake(): void {
    this.createEvent("OnStartEvent").bind(() => this.start())
    this.createEvent("UpdateEvent").bind(() => this.update())
    this.createEvent("OnDestroyEvent").bind(() => this.close())
  }

  /**
   * Wait for somebody else to supply the address.
   *
   * TunnelPairing calls this in onAwake, which always runs before any
   * OnStartEvent, so a lens that gets its hostname from the wearer never
   * tries the inspector's address first. Without it the lens reports a
   * failure to reach a machine it was never going to reach, which reads as
   * a broken connection rather than a missing step.
   */
  holdForPairing(): void {
    this.held = true
  }

  private start(): void {
    this.internet = this.internetModule ?? BridgeClient.tryRequire("InternetModule")
    if (this.internet === null || this.internet === undefined) {
      this.setStatus(
        "failed",
        "no InternetModule. Add one in the Asset Browser and set it on this component."
      )
      return
    }

    const url = (this.bridgeUrl ?? "").trim()
    if (url.length === 0) {
      this.setStatus("failed", "bridgeUrl is empty")
      return
    }
    if (url.indexOf("ws://") !== 0 && url.indexOf("wss://") !== 0) {
      this.setStatus("failed", `bridgeUrl must start with ws:// or wss:// but is "${url}"`)
      return
    }
    if (url.indexOf("127.0.0.1") >= 0 || url.indexOf("localhost") >= 0) {
      print(
        `${TAG}: bridgeUrl points at ${url}. The glasses cannot reach the laptop's loopback address, use its LAN ip.`
      )
    }

    if (this.held) {
      this.setStatus("idle", "waiting for the address from the wearer")
      return
    }
    this.connect()
  }

  /**
   * The url to dial, carrying the pairing code when we already have one.
   *
   * Only relays issue codes, so this is a no op for a direct connection to
   * a FreeCAD on the same network.
   */
  private urlToUse(): string {
    const base = this.bridgeUrl.trim()
    if (this.pairingCode.length === 0) {
      return base
    }
    const separator = base.indexOf("?") >= 0 ? "&" : "?"
    return `${base}${separator}code=${this.pairingCode}`
  }

  /** The relay pairing code, empty on a direct connection. */
  get code(): string {
    return this.pairingCode
  }

  /** Reconnect now, for example after editing bridgeUrl at runtime. */
  reconnect(): void {
    this.close()
    this.closedByUs = false
    this.retryDelay = 1
    this.connect()
  }

  close(): void {
    this.closedByUs = true
    if (this.socket !== null) {
      this.socket.close()
      this.socket = null
    }
  }

  private connect(): void {
    if (this.internet === null) {
      return
    }
    const url = this.urlToUse()
    this.setStatus("connecting", url)

    let socket: WebSocket
    try {
      socket = this.internet.createWebSocket(url)
    } catch (e) {
      const message = `${e}`
      if (message.indexOf("not secure") >= 0 && url.indexOf("ws://") === 0) {
        // The exact state a published build lands in if it still has a
        // development address. Saying so beats repeating the engine.
        this.setStatus(
          "failed",
          "this build cannot use a plain ws:// address. Either turn " +
            "Experimental APIs on for local testing, or give it the tunnel " +
            "words from FreeCAD."
        )
      } else {
        this.setStatus("failed", `createWebSocket rejected "${url}": ${e}`)
      }
      return
    }

    socket.binaryType = "blob"
    this.socket = socket

    socket.onopen = () => {
      this.retryDelay = 1
      this.pingAccumulator = 0
      this.setStatus("connected", url)
      this.send({type: "hello", client: "spectacles", protocol: 1})
    }

    socket.onmessage = (event: WebSocketMessageEvent) => {
      if (typeof event.data === "string") {
        this.handleText(event.data)
      }
      // The bridge only sends text. Binary frames would mean a protocol
      // change, so they are ignored rather than guessed at.
    }

    socket.onerror = () => {
      // onclose follows, which is where the retry is scheduled.
      print(`${TAG}: socket error on ${url}`)
    }

    socket.onclose = (event: WebSocketCloseEvent) => {
      this.socket = null
      if (this.closedByUs) {
        return
      }
      const delay = this.retryDelay
      this.retryDelay = Math.min(this.retryDelay * 2, BridgeClient.MAX_RETRY)
      this.setStatus("retrying", `closed (code ${event.code}), retrying in ${delay} s`)
      this.after(delay, () => {
        if (!this.closedByUs) {
          this.connect()
        }
      })
    }
  }

  private update(): void {
    if (this.pingSeconds <= 0 || !this.isOpen) {
      return
    }
    this.pingAccumulator += getDeltaTime()
    if (this.pingAccumulator >= this.pingSeconds) {
      this.pingAccumulator = 0
      this.send({type: "ping", t: getTime()})
    }
  }

  private send(message: object): void {
    if (this.socket === null) {
      return
    }
    try {
      this.socket.send(JSON.stringify(message))
    } catch (e) {
      print(`${TAG}: send failed: ${e}`)
    }
  }

  private handleText(text: string): void {
    let message: any
    try {
      message = JSON.parse(text)
    } catch (e) {
      print(`${TAG}: unparseable message: ${text.substring(0, 160)}`)
      return
    }

    if (this.verbose) {
      print(`${TAG}: ${text.substring(0, 400)}`)
    }

    switch (message.type) {
      case "model_update": {
        const update = BridgeClient.parseModelUpdate(message)
        if (update === null) {
          print(`${TAG}: ignoring malformed model_update: ${text.substring(0, 200)}`)
          return
        }
        this.onModelUpdate.emit(update)
        return
      }
      case "model_remove": {
        if (typeof message.id === "string") {
          this.onModelRemove.emit(message.id)
        }
        return
      }
      case "hello": {
        print(`${TAG}: ${message.server ?? "bridge"} said hello, protocol ${message.protocol}`)
        if (typeof message.code === "string" && message.code.length > 0) {
          // Remembered so a reconnect rejoins the same pairing. Without
          // this, every time the glasses sleep the user would be handed a
          // new code and have to retype it into FreeCAD.
          this.pairingCode = message.code
          print(`${TAG}: pairing code ${message.code}`)
          this.onPairingCode.emit(message.code)
        }
        return
      }
      case "pong":
        return
      default:
        if (this.verbose) {
          print(`${TAG}: unhandled message type "${message.type}"`)
        }
    }
  }

  /** Validate the wire shape, so a bad push cannot take the lens down. */
  private static parseModelUpdate(raw: any): ModelUpdate | null {
    if (typeof raw.id !== "string" || typeof raw.url !== "string") {
      return null
    }
    if (typeof raw.version !== "number") {
      return null
    }
    const bbox = raw.bbox_mm ?? {}
    const scale = raw.scale ?? {}
    const mode: ScaleMode =
      scale.mode === "ratio" || scale.mode === "fit" ? scale.mode : "true_size"

    return {
      type: "model_update",
      id: raw.id,
      version: raw.version,
      url: raw.url,
      bbox_mm: {
        x: Number(bbox.x) || 0,
        y: Number(bbox.y) || 0,
        z: Number(bbox.z) || 0
      },
      scale: {
        mode: mode,
        factor: Number(scale.factor) || 1,
        target_mm: typeof scale.target_mm === "number" ? scale.target_mm : null
      },
      triangles: Number(raw.triangles) || 0,
      colours: BridgeClient.parseColours(raw.colours),
      offset_mm: BridgeClient.parseOffset(raw.offset_mm),
      cm_per_unit: Number(raw.cm_per_unit) > 0 ? Number(raw.cm_per_unit) : 0,
      pushed_ms: typeof raw.pushed_ms === "number" ? raw.pushed_ms : undefined,
      bytes: typeof raw.bytes === "number" ? raw.bytes : undefined,
      receivedAt: getTime()
    }
  }

  /**
   * Colours as [r, g, b, a] rows, dropping the whole list if any row is
   * unusable.
   *
   * All or nothing on purpose: tinting some meshes and leaving the rest on
   * the template material looks like a rendering bug rather than bad input,
   * and an older addon that sends no colours at all is a case to support.
   */
  /**
   * An offset as a vec3, or null when none arrived.
   *
   * Null rather than the origin, because the two mean different things: an
   * addon that sends no offset wants the lens to centre the mesh itself,
   * while one that sends (0, 0, 0) is saying this part sits exactly on the
   * assembly's origin. Returning the origin for both made every model look
   * as though it had been centred by the addon.
   */
  private static parseOffset(raw: any): vec3 | null {
    if (!Array.isArray(raw) || raw.length < 3) {
      return null
    }
    const out = []
    for (let i = 0; i < 3; i++) {
      const value = Number(raw[i])
      if (!isFinite(value)) {
        return null
      }
      out.push(value)
    }
    return new vec3(out[0], out[1], out[2])
  }

  private static parseColours(raw: any): number[][] {
    if (!Array.isArray(raw)) {
      return []
    }
    const out: number[][] = []
    for (let i = 0; i < raw.length; i++) {
      const row = raw[i]
      if (!Array.isArray(row) || row.length < 3) {
        return []
      }
      const rgba: number[] = []
      for (let c = 0; c < 4; c++) {
        const value = c < row.length ? Number(row[c]) : 1
        if (!isFinite(value)) {
          return []
        }
        rgba.push(Math.min(1, Math.max(0, value)))
      }
      out.push(rgba)
    }
    return out
  }

  private setStatus(state: BridgeState, detail: string): void {
    this.statusValue = {state: state, detail: detail}
    print(`${TAG}: ${state} ${detail}`)
    this.onStatus.emit(this.statusValue)
  }

  private after(seconds: number, fn: () => void): void {
    const event = this.createEvent("DelayedCallbackEvent")
    event.bind(() => fn())
    event.reset(seconds)
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
