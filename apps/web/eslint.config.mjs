// Next's flat config already registers typescript-eslint, so layer our shared rules on top of it rather than
// the base config (which would register the plugin twice).
import next from "eslint-config-next";

const config = [
  { ignores: [".next/**", "next-env.d.ts"] },
  ...next,
  {
    files: ["**/*.ts", "**/*.tsx"],
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "@typescript-eslint/consistent-type-imports": "error",
    },
  },
];

export default config;
