#!/usr/bin/env node
// Check the package in the current directory against the shared tool pins.
import { readFileSync } from 'node:fs';

import { pinMismatches } from './pins.mjs';

/** @type {{ dependencies?: Record<string, string>; devDependencies?: Record<string, string> }} */
const manifest = JSON.parse(readFileSync('package.json', 'utf8'));
const mismatches = pinMismatches({
  ...manifest.dependencies,
  ...manifest.devDependencies,
});
for (const line of mismatches) console.error(line);
process.exitCode = mismatches.length > 0 ? 1 : 0;
