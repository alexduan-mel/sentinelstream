/** Browser APIs Ant Design uses that jsdom lacks. Call from beforeAll in jsdom tests. */
export function installDomStubs(): void {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false
    })
  });
  if (!("ResizeObserver" in window)) {
    class ResizeObserverStub {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
    Object.defineProperty(window, "ResizeObserver", { writable: true, value: ResizeObserverStub });
  }
  // jsdom has no layout, so scrolling is a no-op rather than a "not implemented" error.
  window.scrollTo = (() => undefined) as typeof window.scrollTo;
  // jsdom implements getComputedStyle but throws "not implemented" for pseudo-elements.
  const original = window.getComputedStyle.bind(window);
  window.getComputedStyle = (element: Element) => original(element);
}
