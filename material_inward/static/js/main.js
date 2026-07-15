// Main Dashboard Navigation and Common Functions

// Tab switching functionality
function switchTab(tabName) {
  // Hide all tab panes
  document.querySelectorAll('.tab-pane').forEach(pane => {
    pane.classList.remove('active');
  });
  
  // Remove active class from all nav tabs
  document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.classList.remove('active');
  });
  
  // Show selected tab pane
  const selectedTab = document.getElementById(tabName + 'Tab');
  if (selectedTab) {
    selectedTab.classList.add('active');
  }
  
  // Add active class to clicked nav tab
  if (event && event.target) {
    event.target.classList.add('active');
  }
}

// File upload handling
let uploadedFiles = {
  invoice: null,
  ewaybill: null,
  lr: null
};

function handleFileSelect(docType, inputElement) {
  const file = inputElement.files[0];
  if (file) {
    uploadedFiles[docType] = file;
    
    // Update UI
    const displayElement = document.getElementById(docType + 'Name');
    if (displayElement) {
      displayElement.textContent = file.name;
      displayElement.classList.add('show');
    }
    
    // Update card appearance
    const card = inputElement.closest('.file-upload-card');
    if (card) {
      card.classList.add('has-file');
    }
    
    console.log(`✅ ${docType} selected: ${file.name}`);
    
    // Update upload button state
    updateUploadButton();
  }
}

function updateUploadButton() {
  const uploadButton = document.getElementById('uploadAllButton');
  const hasAnyFile = Object.values(uploadedFiles).some(file => file !== null);
  
  if (uploadButton) {
    uploadButton.disabled = !hasAnyFile;
  }
}

// Upload all files using /process_all endpoint
async function uploadAllFiles() {
  const statusMessage = document.getElementById('statusMessage');
  const uploadButton = document.getElementById('uploadAllButton');
  
  // Check if any files are selected
  const hasAnyFile = Object.values(uploadedFiles).some(file => file !== null);
  if (!hasAnyFile) {
    if (statusMessage) {
      statusMessage.textContent = '⚠ Please select at least one file';
      statusMessage.className = 'status-message error show';
    }
    return;
  }
  
  // Disable button and show processing message
  uploadButton.disabled = true;
  uploadButton.textContent = 'Processing...';
  
  if (statusMessage) {
    statusMessage.textContent = '⏳ Processing documents...';
    statusMessage.className = 'status-message info show';
  }
  
  try {
    // Create FormData with all selected files
    const formData = new FormData();
    
    if (uploadedFiles.invoice) {
      formData.append('invoice', uploadedFiles.invoice);
      console.log('📄 Adding invoice:', uploadedFiles.invoice.name);
    }
    
    if (uploadedFiles.ewaybill) {
      formData.append('ewaybill', uploadedFiles.ewaybill);
      console.log('📄 Adding ewaybill:', uploadedFiles.ewaybill.name);
    }
    
    if (uploadedFiles.lr) {
      formData.append('lr', uploadedFiles.lr);
      console.log('📄 Adding LR:', uploadedFiles.lr.name);
    }
    
    console.log('📤 Uploading to /process_all...');
    
    // Upload all files to /process_all endpoint
    const response = await fetch('/process_all', {
      method: 'POST',
      body: formData
    });
    
    console.log('📥 Response status:', response.status);
    
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    
    const result = await response.json();
    console.log('✅ Server response:', result);
    
    if (result.success) {
      if (statusMessage) {
        statusMessage.textContent = '✅ All documents processed successfully!';
        statusMessage.className = 'status-message success show';
      }
      
      console.log('✅ Upload successful! Reloading page...');
      
      // Reload page to show extracted data
      setTimeout(() => {
        window.location.href = '/view/' + result.history_id;
      }, 1500);
    } else {
      throw new Error(result.error || 'Upload failed');
    }
    
  } catch (error) {
    console.error('❌ Upload error:', error);
    
    if (statusMessage) {
      statusMessage.textContent = `❌ Error: ${error.message}`;
      statusMessage.className = 'status-message error show';
    }
    
    // Re-enable button
    uploadButton.disabled = false;
    uploadButton.textContent = 'Process All Documents';
  }
}

// Clear form helper
function clearForm(formId) {
  const form = document.getElementById(formId);
  if (form) {
    form.reset();
    
    // Special handling for gate in form
    if (formId === 'gateInForm') {
      setCurrentTime();
    }
  }
}

// Set current time for gate in form
function setCurrentTime() {
  const now = new Date();
  const timeString = now.toTimeString().slice(0, 5);
  const dateString = now.toISOString().split('T')[0];
  
  const timeInput = document.getElementById('gateInTime');
  const dateInput = document.getElementById('gateInDate');
  
  if (timeInput) timeInput.value = timeString;
  if (dateInput) dateInput.value = dateString;
}

// Initialize on page load
window.addEventListener('DOMContentLoaded', function() {
  console.log('🚀 Dashboard initialized');
  
  // Set current time for gate in form if it exists
  setCurrentTime();
  
  // Update upload button state
  updateUploadButton();
});