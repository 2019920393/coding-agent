/**
 * 加载动画组件
 * 提供多种加载动画样式
 */

interface LoadingSpinnerProps {
  /** 加载动画类型 */
  type?: "spinner" | "dots" | "bar";
  /** 尺寸 */
  size?: "small" | "medium" | "large";
  /** 自定义类名 */
  className?: string;
}

export function LoadingSpinner({ type = "spinner", size = "medium", className = "" }: LoadingSpinnerProps) {
  if (type === "dots") {
    return (
      <span className={`codo-loading-dots ${className}`}>
        <span className="codo-loading-dots__dot"></span>
        <span className="codo-loading-dots__dot"></span>
        <span className="codo-loading-dots__dot"></span>
      </span>
    );
  }

  if (type === "bar") {
    return (
      <div className={`codo-progress-bar ${className}`}>
        <div className="codo-progress-bar__fill"></div>
      </div>
    );
  }

  // 默认 spinner
  const sizeClass = size !== "medium" ? `codo-loading-spinner--${size}` : "";
  return <span className={`codo-loading-spinner ${sizeClass} ${className}`} aria-label="加载中"></span>;
}
