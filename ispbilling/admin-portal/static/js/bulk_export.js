/* Session 30 — server-side full-data export button toolbar helper.
 * Usage:
 *   BulkExport.mount('#ispEntityExport', '/api/admin/nas/export-full', 'nas_devices');
 * Produces three pill buttons (Excel, CSV, Print) that hit the backend.
 */
window.BulkExport = (function(){
  function trigger(endpoint, fmt){
    var url = endpoint + (endpoint.indexOf('?') === -1 ? '?' : '&') + 'format=' + fmt;
    window.location.href = url;
  }
  function mount(sel, endpoint, stem){
    var $host = (window.jQuery ? jQuery(sel) : null);
    if (!$host || !$host.length) return;
    stem = stem || 'export';
    $host.html(
      '<div class="btn-group" role="group" style="margin:8px 6px;" data-testid="bulk-export-' + stem + '">' +
        '<button type="button" class="btn btn-success btn-sm" data-export="xlsx" data-testid="export-xlsx-' + stem + '">' +
          '<i class="fa fa-file-excel-o"></i> Excel' +
        '</button>' +
        '<button type="button" class="btn btn-primary btn-sm" data-export="csv" data-testid="export-csv-' + stem + '">' +
          '<i class="fa fa-file-text-o"></i> CSV' +
        '</button>' +
      '</div>'
    );
    $host.on('click', 'button[data-export]', function(){
      trigger(endpoint, $(this).data('export'));
    });
  }
  return { mount: mount, trigger: trigger };
})();
