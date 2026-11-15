import { type RefObject, useLayoutEffect, useState } from "react";

/** Largura atual do elemento em pixels, acompanhando o redimensionamento. */
export function useElementWidth(ref: RefObject<HTMLElement | null>, fallback = 600): number {
  const [width, setWidth] = useState(fallback);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const update = () => {
      const value = element.getBoundingClientRect().width;
      if (value > 0) setWidth(Math.round(value));
    };
    update();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}
