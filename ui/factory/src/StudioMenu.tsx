import { useRef, useState, type ReactNode } from "react";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown } from "lucide-react";
import "./styles/controls.css";

export type StudioMenuItem = {
  key?: string;
  label: ReactNode;
  onSelect: () => void;
  disabled?: boolean;
  selected?: boolean;
};

// Native dialogs occupy the browser's top layer. Their popovers must remain
// descendants so both pointer input and the dialog's focus scope can reach them.
export function controlPortalContainer(trigger: HTMLElement | null) {
  return (
    trigger?.closest<HTMLElement>("dialog, [data-overlay-host]") ?? undefined
  );
}

export function StudioMenu({
  label,
  items,
  className = "",
  triggerClassName = "",
  align = "end",
  portalContainer,
  disabled,
  ariaLabel,
}: {
  label: ReactNode;
  items: StudioMenuItem[];
  className?: string;
  triggerClassName?: string;
  align?: "start" | "center" | "end";
  portalContainer?: HTMLElement | null;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [container, setContainer] = useState<HTMLElement>();
  return (
    <Menu.Root
      modal={false}
      onOpenChange={(open) => {
        if (open)
          setContainer(
            portalContainer ?? controlPortalContainer(trigger.current),
          );
      }}
    >
      <Menu.Trigger
        ref={trigger}
        aria-label={ariaLabel}
        disabled={disabled}
        className={`studio-menu-trigger ${triggerClassName}`}
      >
        {label}
        <ChevronDown size={16} aria-hidden="true" />
      </Menu.Trigger>
      <Menu.Portal container={container}>
        <Menu.Content
          className={`studio-menu-content ${className}`}
          align={align}
          sideOffset={6}
          collisionPadding={12}
          loop
        >
          {items.map((item, index) => (
            <Menu.Item
              className="studio-menu-item"
              key={item.key ?? index}
              disabled={item.disabled}
              onSelect={item.onSelect}
              data-selected={item.selected || undefined}
            >
              <span className="studio-option-label">{item.label}</span>
              {item.selected && <Check size={15} aria-hidden="true" />}
            </Menu.Item>
          ))}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
