/**
 * @OnlyCurrentDoc
 */

// Function to create a custom menu when the sheet is opened
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('FEC Search Tools')
    .addItem('Generate Person Search URLs (Manual)', 'generatePersonSearchUrls')
    .addToUi();
}

/**
 * Normalizes a phone number string.
 */
function normalizePhoneNumber(phoneStr) {
  if (!phoneStr || typeof phoneStr !== 'string') {
    return "";
  }
  let digits = phoneStr.replace(/\D/g, '');
  if (digits.length === 11 && digits.startsWith('1')) {
    digits = digits.substring(1); 
  }
  if (digits.length === 10) {
    return `${digits.substring(0, 3)}-${digits.substring(3, 6)}-${digits.substring(6, 10)}`;
  }
  return digits; 
}

// --- Configuration Constants ---
const BASE_URL = "http://76.226.65.32:8080/person"; 
const COL_TIMESTAMP = 1; 
const COL_NAME = 2;      
const COL_EMAIL = 3;     
const COL_STREET = 13;   
const COL_CITY_STATE_ZIP = 14; 
const COL_PHONE = 15;    
const OUTPUT_COLUMN = 20; // Column T

/**
 * Manually generates search URLs for selected rows.
 */
function generatePersonSearchUrls() {
  const ui = SpreadsheetApp.getUi();
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  const selection = sheet.getSelection();

  if (!selection) {
    ui.alert('No cells selected', 'Please select one or more rows containing person data.', ui.ButtonSet.OK);
    return;
  }

  const selectedRange = selection.getActiveRange();
  const firstDataRowInSheet = selectedRange.getRow(); 
  const numSelectedRows = selectedRange.getNumRows();
  
  let urlsGeneratedCount = 0;
  let errorsCount = 0;

  // Ensure output column exists and has correct header
  ensureOutputColumn(sheet);

  for (let i = 0; i < numSelectedRows; i++) {
    const currentRowInSheet = firstDataRowInSheet + i; 
    const result = generateUrlForRow(sheet, currentRowInSheet, OUTPUT_COLUMN);
    if (result === "success") {
      urlsGeneratedCount++;
    } else if (result === "error") {
      errorsCount++;
    }
  }

  displaySummary(ui, urlsGeneratedCount, errorsCount, numSelectedRows);
}

/**
 * Automatically processes a new row, typically from a form submission.
 */
function processNewFormSubmission(e) {
  if (!e) {
    console.error("processNewFormSubmission called without event object.");
    return;
  }

  const sheet = e.range.getSheet(); 
  const newRowNumber = e.range.getRow(); 

  // Ensure output column exists and has correct header
  ensureOutputColumn(sheet);
  
  generateUrlForRow(sheet, newRowNumber, OUTPUT_COLUMN);
}

/**
 * Ensures the output column (T) exists and has the correct header.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet The sheet object.
 */
function ensureOutputColumn(sheet) {
  // Ensure column T exists
  while (sheet.getMaxColumns() < OUTPUT_COLUMN) {
    sheet.insertColumnAfter(sheet.getMaxColumns());
  }
  
  // Set/Correct header if needed
  const headerCell = sheet.getRange(1, OUTPUT_COLUMN);
  if (headerCell.getValue() !== "Generated Search URL") {
    headerCell.setValue("Generated Search URL");
    headerCell.setFontWeight("bold");
  }
}

/**
 * Generates and writes the URL for a single specified row.
 */
function generateUrlForRow(sheet, rowNum, outputColumnIndex) {
  try {
    const fullName = sheet.getRange(rowNum, COL_NAME).getValue() ? String(sheet.getRange(rowNum, COL_NAME).getValue()).trim() : "";
    const email = sheet.getRange(rowNum, COL_EMAIL).getValue() ? String(sheet.getRange(rowNum, COL_EMAIL).getValue()).trim() : "";
    const street = sheet.getRange(rowNum, COL_STREET).getValue() ? String(sheet.getRange(rowNum, COL_STREET).getValue()).trim() : "";
    const cityStateZipRaw = sheet.getRange(rowNum, COL_CITY_STATE_ZIP).getValue() ? String(sheet.getRange(rowNum, COL_CITY_STATE_ZIP).getValue()).trim() : "";
    const rawPhone = sheet.getRange(rowNum, COL_PHONE).getValue() ? String(sheet.getRange(rowNum, COL_PHONE).getValue()).trim() : "";

    const normalizedPhone = normalizePhoneNumber(rawPhone);

    let firstName = "", lastName = "";
    if (fullName) {
      const nameParts = fullName.split(/\s+/).filter(Boolean); 
      if (nameParts.length > 0) {
        firstName = nameParts[0];
        if (nameParts.length > 1) lastName = nameParts.slice(1).join(" ");
      }
    }

    if (!firstName || !lastName) {
      sheet.getRange(rowNum, outputColumnIndex).setValue("Error: First AND Last name required.");
      console.warn("Skipping row " + rowNum + " in " + sheet.getName() + ": Missing first or last name.");
      return "error";
    }

    let city = "", state = "", zip = "";
    if (cityStateZipRaw) {
      let remainingString = cityStateZipRaw;
      const zipMatch = remainingString.match(/\b(\d{5}(?:-\d{4})?)\b/);
      if (zipMatch) {
        zip = zipMatch[0];
        remainingString = remainingString.replace(zipMatch[0], "").trim();
      }
      const stateMatch = remainingString.match(/\b([A-Z]{2})\b/);
      if (stateMatch) {
        state = stateMatch[0];
        const stateRegex = new RegExp("\\b" + state + "\\b", "gi"); // Added 'gi' for global, case-insensitive removal
        remainingString = remainingString.replace(stateRegex, "").trim();
      }
      city = remainingString.replace(/^[\s,]+|[\s,]+$/g, '').replace(/\s\s+/g, ' ').replace(/,$/, '').trim();
    }

    const params = { first: firstName, last: lastName };
    if (email) params.email = email;
    if (street) params.street = street;
    if (city) params.city = city;
    if (state) params.state = state;
    if (zip) params.zip = zip;
    if (normalizedPhone) params.phone = normalizedPhone; 

    const queryParams = Object.keys(params).map(k => encodeURIComponent(k) + '=' + encodeURIComponent(params[k])).join('&');
    const finalUrl = `${BASE_URL}?${queryParams}`;
    const linkText = `Search ${firstName} ${lastName}`;
    
    sheet.getRange(rowNum, outputColumnIndex).setFormula(`=HYPERLINK("${finalUrl}"; "${linkText}")`);
    return "success";

  } catch (err) {
    console.error("Error processing row " + rowNum + " in sheet " + sheet.getName() + ": " + err.message + " Stack: " + err.stack);
    try {
      sheet.getRange(rowNum, outputColumnIndex).setValue("Error: Script failed.");
    } catch (writeErr) {
      console.error("Failed to write error to sheet for row " + rowNum + ": " + writeErr.message);
    }
    return "error";
  }
}

/**
 * Displays a summary message to the user (for manual generation).
 */
function displaySummary(ui, urlsGeneratedCount, errorsCount, numSelectedRows) {
  let message = "";
  if (urlsGeneratedCount > 0) message += `${urlsGeneratedCount} URL(s) generated. `;
  if (errorsCount > 0) message += `${errorsCount} row(s) had errors. `;
  if (urlsGeneratedCount === 0 && errorsCount === 0 && numSelectedRows > 0) {
    message = "No valid data in selected rows or all had issues.";
  } else if (numSelectedRows === 0 && message === "") {
     message = "No rows were selected.";
  }
  ui.alert('Processing Complete', message.trim() || "No data processed.", ui.ButtonSet.OK);
}