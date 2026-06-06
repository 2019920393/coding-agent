import type { CodoWorkbenchApi } from "../shared/ipcTypes";

declare global {
  interface Window {
    codoWorkbench: CodoWorkbenchApi;
  }
}
