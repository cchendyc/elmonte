import { createContext, useContext } from "react";

export type OnboardingContextValue = {
  startTour: () => void;
  isActive: boolean;
};

export const OnboardingContext = createContext<OnboardingContextValue | null>(null);

export function useOnboarding(): OnboardingContextValue {
  const context = useContext(OnboardingContext);
  if (!context) {
    throw new Error("useOnboarding must be used within OnboardingProvider");
  }
  return context;
}
