interface LoadingIndicatorProps {
  variant?: "dots" | "spinner" | "pulse" | "progress";
  size?: "small" | "medium" | "large";
  message?: string;
  progress?: number; // 0-100
}

/**
 * 加载指示器组件，支持多种样式。
 *
 * 变体：
 * - dots: 三点跳动动画
 * - spinner: 旋转圆圈
 * - pulse: 脉动效果
 * - progress: 进度条（需要提供 progress 参数）
 */
export function LoadingIndicator({
  variant = "dots",
  size = "medium",
  message,
  progress
}: LoadingIndicatorProps) {
  return (
    <div className={`codo-loading codo-loading--${size}`}>
      {variant === "dots" && <LoadingDots />}
      {variant === "spinner" && <LoadingSpinner />}
      {variant === "pulse" && <LoadingPulse />}
      {variant === "progress" && <LoadingProgress progress={progress ?? 0} />}
      {message && <span className="codo-loading__message">{message}</span>}
    </div>
  );
}

/**
 * 三点跳动动画
 */
function LoadingDots() {
  return (
    <div className="codo-loading-dots" aria-label="加载中">
      <span className="codo-loading-dots__dot" />
      <span className="codo-loading-dots__dot" />
      <span className="codo-loading-dots__dot" />
    </div>
  );
}

/**
 * 旋转圆圈
 */
function LoadingSpinner() {
  return (
    <div className="codo-loading-spinner" aria-label="加载中">
      <svg viewBox="0 0 50 50" className="codo-loading-spinner__svg">
        <circle
          cx="25"
          cy="25"
          r="20"
          fill="none"
          strokeWidth="4"
          className="codo-loading-spinner__circle"
        />
      </svg>
    </div>
  );
}

/**
 * 脉动效果
 */
function LoadingPulse() {
  return (
    <div className="codo-loading-pulse" aria-label="加载中">
      <div className="codo-loading-pulse__ring" />
      <div className="codo-loading-pulse__ring" />
      <div className="codo-loading-pulse__ring" />
    </div>
  );
}

/**
 * 进度条
 */
function LoadingProgress({ progress }: { progress: number }) {
  const clampedProgress = Math.max(0, Math.min(100, progress));

  return (
    <div className="codo-loading-progress" aria-label={`进度: ${clampedProgress}%`}>
      <div className="codo-loading-progress__track">
        <div
          className="codo-loading-progress__fill"
          style={{ width: `${clampedProgress}%` }}
        />
      </div>
      <span className="codo-loading-progress__label">{clampedProgress}%</span>
    </div>
  );
}

/**
 * Typing 指示器 - 用于聊天场景
 */
export function TypingIndicator() {
  return (
    <div className="codo-typing-indicator" aria-label="正在输入">
      <span className="codo-typing-indicator__dot" />
      <span className="codo-typing-indicator__dot" />
      <span className="codo-typing-indicator__dot" />
    </div>
  );
}

/**
 * 思考指示器 - 用于 AI 思考场景
 */
export function ThinkingIndicator({ message }: { message?: string }) {
  return (
    <div className="codo-thinking-indicator">
      <div className="codo-thinking-indicator__brain">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 2a4 4 0 0 0-4 4c0 1.5.8 2.8 2 3.5V12a4 4 0 0 0 4 4 4 4 0 0 0 4-4V9.5c1.2-.7 2-2 2-3.5a4 4 0 0 0-4-4z" />
          <circle cx="12" cy="18" r="2" />
        </svg>
        <div className="codo-thinking-indicator__waves">
          <span className="codo-thinking-indicator__wave" />
          <span className="codo-thinking-indicator__wave" />
          <span className="codo-thinking-indicator__wave" />
        </div>
      </div>
      <span className="codo-thinking-indicator__message">
        {message ?? "AI 正在思考..."}
      </span>
    </div>
  );
}

/**
 * 骨架屏加载器 - 用于内容占位
 */
export function SkeletonLoader({ lines = 3 }: { lines?: number }) {
  return (
    <div className="codo-skeleton-loader" aria-label="加载中">
      {Array.from({ length: lines }).map((_, index) => (
        <div
          key={index}
          className="codo-skeleton-loader__line"
          style={{ width: `${90 - index * 15}%` }}
        />
      ))}
    </div>
  );
}
