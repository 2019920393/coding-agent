interface ActivityBarProps {
  activeItem: "explorer";
}

/**
 * 左侧 Activity Bar。
 *
 * 工作流：
 * 1. 当前最小 UI 只提供资源管理器入口。
 * 2. 未来新增搜索、版本控制、Agents 时，再扩展 activeItem 联合类型。
 * 3. 组件只负责展示入口，不负责切换业务状态。
 */
export function ActivityBar({ activeItem }: ActivityBarProps) {
  return (
    <nav className="activity-bar" aria-label="工作台导航">
      <button
        className={`activity-bar__item ${
          activeItem === "explorer" ? "activity-bar__item--active" : ""
        }`}
        type="button"
        aria-label="资源管理器"
        aria-current={activeItem === "explorer" ? "page" : undefined}
        title="资源管理器"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M7 3.5h8.2L20 8.3V19a1.5 1.5 0 0 1-1.5 1.5H7A1.5 1.5 0 0 1 5.5 19V5A1.5 1.5 0 0 1 7 3.5Z" />
          <path d="M14.8 3.7v4.8h4.8" />
          <path d="M4 7.5H3A1.5 1.5 0 0 0 1.5 9v10A1.5 1.5 0 0 0 3 20.5h1" />
        </svg>
      </button>
    </nav>
  );
}
