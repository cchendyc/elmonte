import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation } from "react-router-dom";
import {
  TOUR_STEPS,
  hasCompletedOnboarding,
  markOnboardingComplete,
  type TourPlacement,
  type TourStep,
} from "../lib/onboarding";

type SpotlightRect = {
  top: number;
  left: number;
  width: number;
  height: number;
};

type OnboardingContextValue = {
  startTour: () => void;
  isActive: boolean;
};

const OnboardingContext = createContext<OnboardingContextValue | null>(null);

const SPOTLIGHT_PADDING = 8;
const POPOVER_GAP = 14;
const VIEWPORT_MARGIN = 16;

function getTargetElement(target?: string): HTMLElement | null {
  if (!target) return null;
  return document.querySelector<HTMLElement>(`[data-tour="${target}"]`);
}

function measureSpotlight(element: HTMLElement | null): SpotlightRect | null {
  if (!element) return null;

  const rect = element.getBoundingClientRect();
  return {
    top: Math.max(VIEWPORT_MARGIN, rect.top - SPOTLIGHT_PADDING),
    left: Math.max(VIEWPORT_MARGIN, rect.left - SPOTLIGHT_PADDING),
    width: rect.width + SPOTLIGHT_PADDING * 2,
    height: rect.height + SPOTLIGHT_PADDING * 2,
  };
}

function getPopoverPosition(
  spotlight: SpotlightRect | null,
  placement: TourPlacement,
  popover: DOMRect,
): { top: number; left: number } {
  if (!spotlight || placement === "center") {
    return {
      top: Math.max(
        VIEWPORT_MARGIN,
        (window.innerHeight - popover.height) / 2,
      ),
      left: Math.max(
        VIEWPORT_MARGIN,
        (window.innerWidth - popover.width) / 2,
      ),
    };
  }

  const maxLeft = window.innerWidth - popover.width - VIEWPORT_MARGIN;
  const maxTop = window.innerHeight - popover.height - VIEWPORT_MARGIN;

  let top = spotlight.top;
  let left = spotlight.left;

  switch (placement) {
    case "bottom":
      top = spotlight.top + spotlight.height + POPOVER_GAP;
      left = spotlight.left + spotlight.width / 2 - popover.width / 2;
      break;
    case "top":
      top = spotlight.top - popover.height - POPOVER_GAP;
      left = spotlight.left + spotlight.width / 2 - popover.width / 2;
      break;
    case "left":
      top = spotlight.top + spotlight.height / 2 - popover.height / 2;
      left = spotlight.left - popover.width - POPOVER_GAP;
      break;
    case "right":
      top = spotlight.top + spotlight.height / 2 - popover.height / 2;
      left = spotlight.left + spotlight.width + POPOVER_GAP;
      break;
    default:
      break;
  }

  return {
    top: Math.min(Math.max(VIEWPORT_MARGIN, top), maxTop),
    left: Math.min(Math.max(VIEWPORT_MARGIN, left), maxLeft),
  };
}

function TourPopover({
  step,
  stepIndex,
  totalSteps,
  onBack,
  onNext,
  onSkip,
}: {
  step: TourStep;
  stepIndex: number;
  totalSteps: number;
  onBack: () => void;
  onNext: () => void;
  onSkip: () => void;
}) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const placement = step.placement ?? "bottom";

  const updatePosition = useCallback(() => {
    const popover = popoverRef.current;
    if (!popover) return;

    const target = getTargetElement(step.target);
    const spotlight = measureSpotlight(target);
    const nextPosition = getPopoverPosition(
      spotlight,
      placement,
      popover.getBoundingClientRect(),
    );
    setPosition(nextPosition);
  }, [placement, step.target]);

  useLayoutEffect(() => {
    updatePosition();
  }, [updatePosition, stepIndex]);

  useEffect(() => {
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [updatePosition]);

  const isFirst = stepIndex === 0;
  const isLast = stepIndex === totalSteps - 1;

  return (
    <div
      ref={popoverRef}
      className="onboarding-popover"
      style={{ top: position.top, left: position.left }}
      role="dialog"
      aria-labelledby={`onboarding-title-${step.id}`}
      aria-describedby={`onboarding-body-${step.id}`}
    >
      <p className="onboarding-popover__eyebrow">
        Step {stepIndex + 1} of {totalSteps}
      </p>
      <h2
        className="onboarding-popover__title"
        id={`onboarding-title-${step.id}`}
      >
        {step.title}
      </h2>
      <p className="onboarding-popover__body" id={`onboarding-body-${step.id}`}>
        {step.body}
      </p>
      <div className="onboarding-popover__actions">
        <button
          type="button"
          className="onboarding-popover__skip"
          onClick={onSkip}
        >
          Skip tour
        </button>
        <div className="onboarding-popover__nav">
          {!isFirst && (
            <button
              type="button"
              className="onboarding-popover__back"
              onClick={onBack}
            >
              Back
            </button>
          )}
          <button
            type="button"
            className="onboarding-popover__next"
            onClick={onNext}
            autoFocus
          >
            {isLast ? "Done" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}

function TourBlocker({ spotlight }: { spotlight: SpotlightRect | null }) {
  if (!spotlight) {
    return <div className="onboarding-blocker onboarding-blocker--full" />;
  }

  const { top, left, width, height } = spotlight;
  const bottom = top + height;
  const right = left + width;

  return (
    <>
      <div
        className="onboarding-blocker"
        style={{ top: 0, left: 0, right: 0, height: top }}
      />
      <div
        className="onboarding-blocker"
        style={{ top: bottom, left: 0, right: 0, bottom: 0 }}
      />
      <div
        className="onboarding-blocker"
        style={{ top, left: 0, width: left, height }}
      />
      <div
        className="onboarding-blocker"
        style={{ top, left: right, right: 0, height }}
      />
    </>
  );
}

function TourOverlay({
  step,
  stepIndex,
  onBack,
  onNext,
  onSkip,
}: {
  step: TourStep;
  stepIndex: number;
  onBack: () => void;
  onNext: () => void;
  onSkip: () => void;
}) {
  const [spotlight, setSpotlight] = useState<SpotlightRect | null>(null);

  const updateSpotlight = useCallback(() => {
    const target = getTargetElement(step.target);
    setSpotlight(measureSpotlight(target));
  }, [step.target]);

  useLayoutEffect(() => {
    updateSpotlight();
  }, [updateSpotlight, stepIndex]);

  useEffect(() => {
    window.addEventListener("resize", updateSpotlight);
    window.addEventListener("scroll", updateSpotlight, true);
    return () => {
      window.removeEventListener("resize", updateSpotlight);
      window.removeEventListener("scroll", updateSpotlight, true);
    };
  }, [updateSpotlight]);

  return (
    <div className="onboarding-overlay" aria-hidden={false}>
      <TourBlocker spotlight={spotlight} />
      {spotlight && (
        <div
          className="onboarding-spotlight"
          style={{
            top: spotlight.top,
            left: spotlight.left,
            width: spotlight.width,
            height: spotlight.height,
          }}
        />
      )}
      <TourPopover
        step={step}
        stepIndex={stepIndex}
        totalSteps={TOUR_STEPS.length}
        onBack={onBack}
        onNext={onNext}
        onSkip={onSkip}
      />
    </div>
  );
}

export function OnboardingProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [isActive, setIsActive] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const autoStartedRef = useRef(false);

  const finishTour = useCallback(() => {
    setIsActive(false);
    setStepIndex(0);
    markOnboardingComplete();
  }, []);

  const startTour = useCallback(() => {
    setStepIndex(0);
    setIsActive(true);
  }, []);

  useEffect(() => {
    if (location.pathname !== "/") return;
    if (autoStartedRef.current || hasCompletedOnboarding()) return;

    autoStartedRef.current = true;
    const timer = window.setTimeout(() => startTour(), 600);
    return () => window.clearTimeout(timer);
  }, [location.pathname, startTour]);

  useEffect(() => {
    if (!isActive) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") finishTour();
    };

    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [finishTour, isActive]);

  const goNext = useCallback(() => {
    if (stepIndex >= TOUR_STEPS.length - 1) {
      finishTour();
      return;
    }
    setStepIndex((current) => current + 1);
  }, [finishTour, stepIndex]);

  const goBack = useCallback(() => {
    setStepIndex((current) => Math.max(0, current - 1));
  }, []);

  const step = TOUR_STEPS[stepIndex];

  return (
    <OnboardingContext.Provider value={{ startTour, isActive }}>
      {children}
      {isActive && step && location.pathname === "/" && (
        <TourOverlay
          step={step}
          stepIndex={stepIndex}
          onBack={goBack}
          onNext={goNext}
          onSkip={finishTour}
        />
      )}
    </OnboardingContext.Provider>
  );
}

export function useOnboarding(): OnboardingContextValue {
  const context = useContext(OnboardingContext);
  if (!context) {
    throw new Error("useOnboarding must be used within OnboardingProvider");
  }
  return context;
}
