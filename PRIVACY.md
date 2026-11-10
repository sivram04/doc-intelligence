# Privacy policy

_Last updated: 9 November 2026_

DocIntel is an open-source portfolio project. This page explains what the **public demo**
(https://docintel-408632234593.us-central1.run.app) does with what you give it. A copy you run on your own computer
sends nothing anywhere: documents, results and corrections stay on that machine.

## No account, no tracking
You can use the demo without an account, sign-in, email address or payment. It uses **no advertising or analytics
cookies** and no third-party trackers.

## What is stored in your browser
- **One cookie** with a random ID that identifies your workspace, so you see your own documents and nobody else
  does. It expires after 24 hours.
- **Local storage** for small conveniences (for example the reviewer name you type).

## What is sent to the server
- **Documents you upload.** They are read to extract their fields (invoices) or to write a summary and answer
  questions (reports). To do this, the page text and images are sent to **Google's Gemini API**. Please upload only
  **sample or public documents**, never personal or confidential ones.
- **Questions you ask** about a report go to the same server and to the Gemini API.
- **Where it is kept.** Files, results and corrections stay in the server's temporary storage, visible only to the
  workspace that uploaded them, and are **deleted after 24 hours**. They are also lost whenever the server restarts.

## Daily limits
To keep the demo free, each visitor can process 2 documents and 2 reports a day. To enforce this without accounts,
the server stores a **salted, one-way hash** of your IP address next to your uploads, never the address itself. The
hash is deleted with the uploads after 24 hours.

## Logs
The hosting platform (**Google Cloud Run**) records standard request logs: time, path, response status, and technical
details such as IP address and browser type. They are used only to keep the demo running and fix errors, and are kept
under Google Cloud's default retention (about 30 days).

## Processors
- **Google Cloud** (hosting, temporary storage, logs) and **Google's Gemini API** (reading documents and answering
  questions), under Google's terms and data-processing commitments.

## Contact
If you believe you sent personal information through the demo and want it removed before it expires, open an issue on
the [GitHub repository](https://github.com/sivram04/doc-intelligence/issues) without including the information itself.
