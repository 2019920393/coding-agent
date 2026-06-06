"use strict";

// electron/preload.ts
var import_electron = require("electron");
var API_KEY = "codoWorkbench";
var CodoWorkbenchPreload = class {
  expose() {
    import_electron.contextBridge.exposeInMainWorld(API_KEY, this.createApi());
  }
  createApi() {
    return {
      workspace: {
        selectWorkspace: () => this.selectWorkspace(),
        listDirectory: (relativePath) => this.listDirectory(relativePath),
        readFile: (relativePath) => this.readFile(relativePath),
        writeFile: (request) => this.writeFile(request)
      },
      ai: {
        listSessions: (request) => this.listSessions(request),
        loadSessionMessages: (request) => this.loadSessionMessages(request),
        deleteSession: (request) => this.deleteSession(request),
        submitMessage: (request) => this.submitMessage(request),
        cancelTurn: (request) => this.cancelTurn(request),
        resolveInteraction: (request) => this.resolveInteraction(request),
        onEvent: (listener) => this.onEvent(listener)
      }
    };
  }
  /**
   * 打开系统目录选择器，让用户显式授权一个 workspace。
   */
  async selectWorkspace() {
    return import_electron.ipcRenderer.invoke("workspace:select");
  }
  /**
   * 读取 workspace 内某个目录的第一层内容。
   *
   * 工作流：
   * 1. 前端传入相对 workspace 的路径。
   * 2. main process 校验路径边界。
   * 3. 返回这一层的文件和文件夹，不递归读取。
   */
  async listDirectory(relativePath) {
    return import_electron.ipcRenderer.invoke("fs:list-directory", relativePath);
  }
  /**
   * 读取 workspace 内单个文件内容。
   *
   * 工作流：
   * 1. 用户点击文件后才调用。
   * 2. main process 校验路径边界。
   * 3. 返回文件名、相对路径和文本内容。
   */
  async readFile(relativePath) {
    return import_electron.ipcRenderer.invoke("fs:read-file", relativePath);
  }
  /**
   * 写入 workspace 内单个文件内容。
   *
   * 工作流：
   * 1. Renderer 传入相对路径和编辑器当前内容。
   * 2. preload 不暴露 Node fs，只把请求转给 main process。
   * 3. main process 完成路径边界校验和真实写入。
   */
  async writeFile(request) {
    return import_electron.ipcRenderer.invoke("fs:write-file", request);
  }
  /**
   * 列出指定工作区的历史 AI 会话。
   */
  async listSessions(request) {
    return import_electron.ipcRenderer.invoke("ai:list-sessions", request);
  }
  /**
   * 读取历史会话消息。
   */
  async loadSessionMessages(request) {
    return import_electron.ipcRenderer.invoke("ai:load-session-messages", request);
  }
  /**
   * 删除历史会话。
   */
  async deleteSession(request) {
    await import_electron.ipcRenderer.invoke("ai:delete-session", request);
  }
  /**
   * 提交一轮 AI 对话给主进程。
   */
  async submitMessage(request) {
    await import_electron.ipcRenderer.invoke("ai:submit-message", request);
  }
  /**
   * 中断当前 AI 轮次。
   */
  async cancelTurn(request) {
    await import_electron.ipcRenderer.invoke("ai:cancel-turn", request);
  }
  /**
   * 回答 AI runtime 的交互请求。
   */
  async resolveInteraction(request) {
    await import_electron.ipcRenderer.invoke("ai:resolve-interaction", request);
  }
  /**
   * 订阅 AI 事件流。
   */
  onEvent(listener) {
    const channel = "ai:event";
    const handleEvent = (_event, payload) => {
      listener(payload);
    };
    import_electron.ipcRenderer.on(channel, handleEvent);
    return () => {
      import_electron.ipcRenderer.off(channel, handleEvent);
    };
  }
};
new CodoWorkbenchPreload().expose();
