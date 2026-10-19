import {
  Children,
  Fragment,
  isValidElement,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ComponentPropsWithoutRef,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";
import * as Select from "@radix-ui/react-select";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown, ChevronUp } from "lucide-react";
import { controlPortalContainer } from "./StudioMenu";
import { lockPageScroll } from "./ScrollLock";
import "./styles/controls.css";

type Option = {
  value: string;
  label: ReactNode;
  disabled?: boolean;
  group?: string;
};
export type StudioSelectProps = SelectHTMLAttributes<HTMLSelectElement> & {
  portalContainer?: HTMLElement | null;
  placeholder?: string;
};

function optionText(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) =>
      isValidElement<{ children?: ReactNode }>(child)
        ? optionText(child.props.children)
        : String(child),
    )
    .join("");
}
function readOptions(
  children: ReactNode,
  group?: string,
  disabled = false,
): Option[] {
  return Children.toArray(children).flatMap((node): Option[] => {
    if (
      !isValidElement<{
        children?: ReactNode;
        value?: string | number;
        disabled?: boolean;
        label?: string;
      }>(node)
    )
      return [];
    if (node.type === "option")
      return [
        {
          value: String(node.props.value ?? optionText(node.props.children)),
          label: node.props.label ?? node.props.children,
          disabled: disabled || node.props.disabled,
          group,
        },
      ];
    if (node.type === "optgroup")
      return readOptions(
        node.props.children,
        node.props.label,
        disabled || !!node.props.disabled,
      );
    if (node.type === Fragment)
      return readOptions(node.props.children, group, disabled);
    return [];
  });
}
const encode = (value: string) => "option:" + value;

/** A Radix control backed by a real select for native form semantics. */
export function StudioSelect({
  children,
  value,
  defaultValue,
  onChange,
  multiple,
  name,
  required,
  disabled,
  id,
  className = "",
  style,
  form,
  portalContainer,
  placeholder = "请选择",
  onInvalid,
  ...rest
}: StudioSelectProps) {
  const options = readOptions(children);
  const initial = () => {
    const source =
      defaultValue ??
      (multiple ? [] : (options.find((item) => !item.disabled)?.value ?? ""));
    return Array.isArray(source) ? source.map(String) : [String(source)];
  };
  const [local, setLocal] = useState<string[]>(initial);
  const requested =
    value === undefined
      ? local
      : Array.isArray(value)
        ? value.map(String)
        : [String(value)];
  // Match a native single select when asynchronously loaded options replace an
  // empty or removed choice: its first enabled option becomes the form value.
  const selected =
    !multiple &&
    options.length &&
    !options.some((item) => requested.includes(item.value))
      ? [options.find((item) => !item.disabled)?.value ?? ""]
      : requested;
  const native = useRef<HTMLSelectElement>(null);
  const resetting = useRef(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const releaseScroll = useRef<(() => void) | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const [container, setContainer] = useState<HTMLElement>();

  // The native select remains the single form entry, supports external forms,
  // constraint validation, fieldset disabling, reset and native change events.
  useEffect(() => {
    const element = native.current;
    const owner = element?.form;
    if (!element || !owner) return;
    const reset = (event: Event) => {
      resetting.current = true;
      // Radix also listens for form resets; the adapter owns the real form
      // value, including canceled resets and externally controlled fields.
      queueMicrotask(() => {
        resetting.current = false;
        if (event.defaultPrevented) return;
        const next = value === undefined ? initial() : selected;
        if (value === undefined) setLocal(next);
        for (const option of Array.from(element.options))
          option.selected = next.includes(option.value);
        setInvalid(false);
      });
    };
    owner.addEventListener("reset", reset, true);
    return () => owner.removeEventListener("reset", reset, true);
  });
  useEffect(() => () => releaseScroll.current?.(), []);
  useEffect(() => {
    if (!open && releaseScroll.current) {
      releaseScroll.current();
      releaseScroll.current = undefined;
    }
  }, [open]);

  const change = (event: ChangeEvent<HTMLSelectElement>) => {
    const next = Array.from(
      event.target.selectedOptions,
      (option) => option.value,
    );
    setLocal(next);
    setInvalid(false);
    onChange?.(event);
  };
  const choose = (next: string[]) => {
    const element = native.current;
    if (!element) return;
    for (const option of Array.from(element.options))
      option.selected = next.includes(option.value);
    element.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const changeOpen = (next: boolean) => {
    if (next) {
      setContainer(portalContainer ?? controlPortalContainer(trigger.current));
      // Radix Select locks body scrolling. Reserve the existing document gutter
      // first so its own compensation sees no additional gap and cannot resize it.
      if (!multiple && !releaseScroll.current)
        releaseScroll.current = lockPageScroll();
    }
    setOpen(next);
  };
  const triggerProps = {
    ...rest,
    ref: trigger,
    id,
    style,
    disabled,
    "aria-invalid": rest["aria-invalid"] ?? (invalid || undefined),
    "aria-required": required,
    className: `studio-select-trigger ${className}`,
  } as ComponentPropsWithoutRef<typeof Select.Trigger> & {
    ref: typeof trigger;
  };
  const selectedLabels = options.filter((item) =>
    selected.includes(item.value),
  );
  const content = options.map((option, index) => {
    const label =
      option.group && option.group !== options[index - 1]?.group ? (
        <div className="studio-option-group" key={`group-${index}`}>
          {option.group}
        </div>
      ) : null;
    return (
      <Fragment key={option.value}>
        {label}
        {multiple ? (
          <Menu.CheckboxItem
            className="studio-menu-item"
            checked={selected.includes(option.value)}
            disabled={option.disabled}
            onSelect={(event) => event.preventDefault()}
            onCheckedChange={(checked) =>
              choose(
                checked
                  ? [...selected, option.value]
                  : selected.filter((value) => value !== option.value),
              )
            }
            textValue={optionText(option.label)}
          >
            <span className="studio-option-label">{option.label}</span>
            <Menu.ItemIndicator>
              <Check size={15} aria-hidden="true" />
            </Menu.ItemIndicator>
          </Menu.CheckboxItem>
        ) : (
          <Select.Item
            className="studio-select-item"
            value={encode(option.value)}
            disabled={option.disabled}
            textValue={optionText(option.label)}
          >
            <Select.ItemText>{option.label}</Select.ItemText>
            <Select.ItemIndicator>
              <Check size={15} aria-hidden="true" />
            </Select.ItemIndicator>
          </Select.Item>
        )}
      </Fragment>
    );
  });
  return (
    <>
      <select
        {...rest}
        ref={native}
        id={id ? `${id}-native` : undefined}
        className="studio-select-native"
        aria-hidden="true"
        tabIndex={-1}
        name={name}
        form={form}
        multiple={multiple}
        required={required}
        disabled={disabled}
        value={multiple ? selected : (selected[0] ?? "")}
        onChange={change}
        onInvalid={(event) => {
          event.preventDefault();
          setInvalid(true);
          trigger.current?.focus({ preventScroll: true });
          onInvalid?.(event);
        }}
      >
        {children}
      </select>
      {multiple ? (
        <Menu.Root modal={false} open={open} onOpenChange={changeOpen}>
          <Menu.Trigger
            {...triggerProps}
            role="combobox"
            aria-expanded={open}
            aria-multiselectable="true"
          >
            <span className="studio-select-value">
              {selectedLabels.length
                ? selectedLabels
                    .map((item) => optionText(item.label))
                    .join("、")
                : placeholder}
            </span>
            <ChevronDown size={16} aria-hidden="true" />
          </Menu.Trigger>
          <Menu.Portal container={container}>
            <Menu.Content
              className="studio-menu-content studio-select-content"
              sideOffset={6}
              collisionPadding={12}
              align="start"
              loop
            >
              {content}
            </Menu.Content>
          </Menu.Portal>
        </Menu.Root>
      ) : (
        <Select.Root
          value={encode(selected[0] ?? "")}
          open={open}
          onOpenChange={changeOpen}
          onValueChange={(next) => {
            if (!resetting.current) choose([next.slice(7)]);
          }}
          disabled={disabled}
          required={required}
        >
          <Select.Trigger {...triggerProps}>
            <span className="studio-select-value">
              {selectedLabels[0]?.label ?? placeholder}
            </span>
            <Select.Icon>
              <ChevronDown size={16} aria-hidden="true" />
            </Select.Icon>
          </Select.Trigger>
          <Select.Portal container={container}>
            <Select.Content
              className="studio-select-content"
              position="popper"
              sideOffset={6}
              collisionPadding={12}
            >
              <Select.ScrollUpButton className="studio-select-scroll">
                <ChevronUp size={15} />
              </Select.ScrollUpButton>
              <Select.Viewport>{content}</Select.Viewport>
              <Select.ScrollDownButton className="studio-select-scroll">
                <ChevronDown size={15} />
              </Select.ScrollDownButton>
            </Select.Content>
          </Select.Portal>
        </Select.Root>
      )}
    </>
  );
}
