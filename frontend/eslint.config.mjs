import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    files: ["**/*.{js,jsx,mjs,ts,tsx,mts,cts}"],
    rules: {
      // Telemetry-derived strings must only ever render as escaped text (SECURITY.md §5, T41).
      "react/no-danger": "error",
      "no-restricted-syntax": [
        "error",
        {
          selector: "MemberExpression[object.object.name='process'][object.property.name='env'][property.name=/^NEXT_PUBLIC_/]",
          message: "NEXT_PUBLIC_* variables are inlined into the browser bundle; the dashboard uses none (CR-06).",
        },
      ],
    },
  },
  {
    // CommonJS preload for the unit-test runner (tests/setup/register.cjs).
    files: ["**/*.cjs"],
    rules: { "@typescript-eslint/no-require-imports": "off" },
  },
  globalIgnores([".next/**", "out/**", "build/**", ".test-build/**", "next-env.d.ts", "playwright-report/**", "test-results/**"]),
]);
