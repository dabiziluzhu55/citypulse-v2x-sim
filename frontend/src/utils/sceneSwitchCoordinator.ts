export interface SceneSwitchTransaction {
  intersectionId: string
  revision: number
  signal: AbortSignal
}

export class SceneSwitchCoordinator {
  private revision = 0
  private active: { token: SceneSwitchTransaction; controller: AbortController } | null = null

  begin(intersectionId: string): SceneSwitchTransaction {
    this.active?.controller.abort()
    const controller = new AbortController()
    const token = {
      intersectionId,
      revision: ++this.revision,
      signal: controller.signal,
    }
    this.active = { token, controller }
    return token
  }

  isCurrent(token: SceneSwitchTransaction): boolean {
    return this.active?.token === token && !token.signal.aborted
  }

  complete(token: SceneSwitchTransaction): boolean {
    if (!this.isCurrent(token)) return false
    this.active = null
    return true
  }

  cancel(): void {
    this.active?.controller.abort()
    this.active = null
  }
}
