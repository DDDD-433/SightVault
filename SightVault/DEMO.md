# Two-minute SightVault demo

On this Mac, double-click `Start-SightVault.command` and open <http://127.0.0.1:7861/>. Before presenting, launch the app, run one search and one analysis to warm the models, then disconnect Wi-Fi. The demo collection should already show 62 indexed images.

1. Choose **Visual meaning**. Search **GPU memory problem**. Open the CUDA screenshot. Point out that meaning-based retrieval found the actual image.
2. Choose **Hybrid**. Search **Find ERESOLVE**. Show the OCR match badge and exact error text in the evidence panel.
3. Search **CUDA out of memory**, then choose **First occurrence**. Show the earliest indexed screenshot and its source timestamp. The dates in the demo are fictional file dates.
4. Open the CUDA screenshot. Ask **What probably caused this error?** and click **Analyze with MiniCPM-V**. Show the answer alongside the image and extracted text.
5. Search **Find the image with a red backpack** in **Visual meaning**. The red backpack is an illustration with no “red backpack” text embedded in it.
6. Finish on the status panel: local models, 62 indexed images, measured retrieval time, zero cloud API calls.

Pitch: “I vaguely remember seeing a GPU error. SightVault finds the screenshot, recovers the exact text, shows when the file first appeared in my collection, and explains the evidence locally.”

Optional: index your own small folder using RapidOCR, repeat the search, then re-index it to demonstrate safe duplicate skipping.

> Before presenting: launch and run one search to warm the model (see MAC_QA_REPORT.md for this Mac’s measured timings). After 'First occurrence', new searches automatically return to relevance order.
