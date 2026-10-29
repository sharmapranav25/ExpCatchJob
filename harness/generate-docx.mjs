#!/usr/bin/env node
// Converts a career-ops tailored-CV HTML file (the same one generate-pdf.mjs renders) to a .docx,
// so "View Resume" can hand the candidate an editable Word file instead of a flattened PDF.
//
// Known limitation (checked 2026-10-27): html-to-docx renders adjacent inline <span>s on one line
// (e.g. the pranav template's "Company | Role" job-line) each on its own paragraph, so a literal " | "
// separator can land on its own line in Word. Every word of content comes through correctly -- this is
// a layout quirk, not a fact-accuracy issue -- but it means the .docx is not a pixel copy of the PDF.
//
// Usage: node local/harness/generate-docx.mjs <input.html> <output.docx>
import { readFile, writeFile } from 'node:fs/promises';
import HTMLtoDOCX from 'html-to-docx';

const [, , inPath, outPath] = process.argv;
if (!inPath || !outPath) {
  console.error('Usage: node local/harness/generate-docx.mjs <input.html> <output.docx>');
  process.exit(1);
}

const html = await readFile(inPath, 'utf8');
const buffer = await HTMLtoDOCX(html, null, {
  table: { row: { cantSplit: true } },
  footer: false,
  pageNumber: false,
  margins: { top: 720, right: 720, bottom: 720, left: 720 }, // 0.5in, matches the pranav template
});
await writeFile(outPath, buffer);
console.log(`DOCX generated: ${outPath} (${buffer.length} bytes)`);
