import type {
  AiBridgeEvent,
  AiCancelTurnRequest,
  AiLoadSessionMessagesRequest,
  AiListSessionsRequest,
  AiResolveInteractionRequest,
  AiSessionInfo,
  AiSessionMessage,
  AiSubmitMessageRequest
} from "../../shared/aiProtocol";
import type { CodoAiApi } from "../../shared/ipcTypes";

/**
 * Renderer 侧 AI 客户端。
 *
 * 工作流：
 * 1. React 组件不直接碰 window.codoWorkbench.ai。
 * 2. 客户端统一负责提交 prompt、取消当前轮次和订阅事件。
 * 3. 这样后续替换桥接实现时，组件层不用跟着改。
 */
export class AiClient {
  public constructor(private readonly api: CodoAiApi) {}

  /**
   * 读取当前工作区的历史会话列表。
   */
  public listSessions(request: AiListSessionsRequest): Promise<AiSessionInfo[]> {
    return this.api.listSessions(request);
  }

  /**
   * 读取某个历史会话的可展示消息。
   */
  public loadSessionMessages(
    request: AiLoadSessionMessagesRequest
  ): Promise<AiSessionMessage[]> {
    return this.api.loadSessionMessages(request);
  }

  /**
   * 提交一轮 AI 对话。
   */
  public submitMessage(request: AiSubmitMessageRequest): Promise<void> {
    return this.api.submitMessage(request);
  }

  /**
   * 中断指定 turn。
   */
  public cancelTurn(request: AiCancelTurnRequest): Promise<void> {
    return this.api.cancelTurn(request);
  }

  /**
   * 回答 runtime 交互请求。
   */
  public resolveInteraction(request: AiResolveInteractionRequest): Promise<void> {
    return this.api.resolveInteraction(request);
  }

  /**
   * 订阅桥接事件。
   */
  public onEvent(listener: (event: AiBridgeEvent) => void): () => void {
    return this.api.onEvent(listener);
  }
}

/**
 * 从 preload 暴露的 window API 创建 AI 客户端。
 */
export function createAiClient(): AiClient {
  return new AiClient(window.codoWorkbench.ai);
}
