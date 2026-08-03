// Extracted Data Tab Functionality - Clean Version

function switchDataTab(dataType) {
  console.log('Switching to tab:', dataType);
  
  // Hide all data content sections
  document.querySelectorAll('.tab-content').forEach(tab => {
    tab.classList.remove('active');
  });
  
  // Remove active class from all data tab buttons
  document.querySelectorAll('.content-tabs .tab-button').forEach(btn => {
    btn.classList.remove('active');
  });
  
  // Show selected data section
  const dataSection = document.getElementById(dataType + 'Data');
  if (dataSection) {
    dataSection.classList.add('active');
    console.log('Activated tab:', dataType + 'Data');
  } else {
    console.error('Tab not found:', dataType + 'Data');
  }
  
  // Add active class to clicked button
  const clickedButton = Array.from(document.querySelectorAll('.content-tabs .tab-button'))
    .find(btn => {
      const btnText = btn.textContent.toLowerCase();
      if (dataType === 'invoice') return btnText.includes('invoice') && !btnText.includes('e-way');
      if (dataType === 'ewaybill') return btnText.includes('e-way');
      if (dataType === 'lr') return btnText.includes('lr');
      return false;
    });
  
  if (clickedButton) {
    clickedButton.classList.add('active');
  }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
  console.log('Extracted data module loaded');
  
  // Add click handlers to tab buttons
  const tabButtons = document.querySelectorAll('.content-tabs .tab-button');
  
  tabButtons.forEach(button => {
    button.addEventListener('click', function(e) {
      e.preventDefault();
      
      const buttonText = this.textContent.toLowerCase();
      let dataType = '';
      
      // Determine which tab was clicked
      if (buttonText.includes('invoice') && !buttonText.includes('e-way')) {
        dataType = 'invoice';
      } else if (buttonText.includes('e-way')) {
        dataType = 'ewaybill';
      } else if (buttonText.includes('lr')) {
        dataType = 'lr';
      }
      
      if (dataType) {
        console.log('Tab clicked:', dataType);
        switchDataTab(dataType);
      }
    });
  });
  
  // Show the first active tab on load
  const firstActiveButton = document.querySelector('.content-tabs .tab-button.active');
  if (firstActiveButton) {
    const buttonText = firstActiveButton.textContent.toLowerCase();
    let initialTab = '';
    
    if (buttonText.includes('invoice') && !buttonText.includes('e-way')) {
      initialTab = 'invoice';
    } else if (buttonText.includes('e-way')) {
      initialTab = 'ewaybill';
    } else if (buttonText.includes('lr')) {
      initialTab = 'lr';
    }
    
    if (initialTab) {
      console.log('Initial tab:', initialTab);
      // Small delay to ensure DOM is fully ready
      setTimeout(() => switchDataTab(initialTab), 100);
    }
  }
});

// Helper function to display data in a formatted way
function displayDataItem(label, value) {
  if (!value || value === '') {
    return '';
  }
  
  return `
    <div class="data-item">
      <div class="data-item-label">${label}</div>
      <div class="data-item-value">${value}</div>
    </div>
  `;
}

// Helper function to display HSN details table
function displayHSNTable(hsnDetails) {
  if (!hsnDetails || hsnDetails.length === 0) {
    return '<p style="color: #999; font-style: italic;">No HSN details available</p>';
  }
  
  let tableHTML = `
    <div class="hsn-table-wrapper">
      <table class="hsn-table">
        <thead>
          <tr>
            <th>HSN/SAC</th>
            <th>Description</th>
            <th>Quantity</th>
            <th>Taxable Value</th>
            <th>CGST Rate</th>
            <th>CGST Amount</th>
            <th>SGST Rate</th>
            <th>SGST Amount</th>
            <th>IGST Rate</th>
            <th>IGST Amount</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody>
  `;
  
  hsnDetails.forEach(item => {
    tableHTML += `
      <tr>
        <td>${item['HSN/SAC'] || '-'}</td>
        <td>${item['Description'] || '-'}</td>
        <td>${item['Quantity'] || '-'}</td>
        <td>${item['Taxable Value'] || '-'}</td>
        <td>${item['CGST Rate'] || '-'}</td>
        <td>${item['CGST Amount'] || '-'}</td>
        <td>${item['SGST Rate'] || '-'}</td>
        <td>${item['SGST Amount'] || '-'}</td>
        <td>${item['IGST Rate'] || '-'}</td>
        <td>${item['IGST Amount'] || '-'}</td>
        <td>${item['Total'] || '-'}</td>
      </tr>
    `;
  });
  
  tableHTML += `
        </tbody>
      </table>
    </div>
  `;
  
  return tableHTML;
}