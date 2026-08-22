// to support older versions of IE
if (!window.console) { window.console = {}; window.console.log = function () { }; };

var PrintService = function () {
    var baseUrl = null;

    // parameters from ACC
    var pSessionKey = null;
    var pSessionValue = null;
    var pFilename = null;
    var pURL = null;
    var pToken = null;
    var pParam = null;
	var pCustom = null;
	var pCustomHeader = null;
	var pCustomParam = null;
	var pAlias = null;
	var pVerifyString = null;

    // private variables
    var lastRunVersion = null;
    var onAjaxError = null;

    // check if browser is IE9 or lower
    function isUnsupportedBrowser() {
        var ua = window.navigator.userAgent;
        var msie = ua.indexOf("MSIE ");

        if (msie > 0) {
            if (parseInt(ua.substring(msie + 5, ua.indexOf(".", msie))) < 10) {
                return true;
            }
        }

        return false;
    }

    function getBaseUrl() {
        if (baseUrl == null) {
            throw new Error("Print control base URL not set. call init() first.");
        }

        return baseUrl;
    }

    function setLastRunVersion(appVersion) {
        if (!window.localStorage) {
            return;
        }
		
        localStorage["printControl.lastRunVersion"] = appVersion;
        lastRunVersion = appVersion;
    }

    function ajaxSetup(ajaxParams) {
		// for IE support
        $.support.cors = true;

        if (ajaxParams.type == undefined) {
            // add a dynamic parameter as a workaround to avoid caching
            var now = new Date();
            var cacheParam = "_=" + now.getTime();
            if (ajaxParams.url.indexOf("?") >= 0) {
                ajaxParams.url += "&" + cacheParam;
            } else {
                ajaxParams.url += "?" + cacheParam;
            }

            if (!isUnsupportedBrowser()) {
                return $.getJSON(ajaxParams.url, ajaxParams.data).fail(function (jqXHR) {
                    if (onAjaxError != null) {
                        onAjaxError(jqXHR, textStatus);
                    }
                });
			}
        }
		
        return $.ajax({
            url: ajaxParams.url,
            data: ajaxParams.data,
            cache: false,
            timeout: 300000,
            async: true,
            contentType: 'text/plain', // CORS; for older versions of IE
            type: ajaxParams.type,
            dataType: 'json'
        }).fail(function (jqXHR, textStatus) {
           if (onAjaxError != null && jqXHR.responseText != "") {
              onAjaxError(jqXHR, textStatus);
           }
        });
    }

    // save the app version after any ajax call
    function printServiceAjax(ajaxParams) {
        if (lastRunVersion != null) {
            return ajaxSetup(ajaxParams);
        } else {
            return ajaxSetup(ajaxParams).done(function () {
                PrintService.ajaxGetPrintControlVersion().done(function (data) {
					if (data.appVersion != undefined)
						setLastRunVersion(data.appVersion);
                });
            });
        }
    }

    function ajaxMonitorPrintJob(printerName, jobId, callback, printJob) {
        return printServiceAjax({
            url: getBaseUrl() + "monitorJobStatus",
            data: {
                printerName: printerName,
                jobId: jobId,
                jobStatus: printJob == null ? null : printJob.jobStatus,
                numberOfPagesPrinted: printJob == null ? 0 : printJob.numberOfPagesPrinted
            }
        }).done(function (data) {
            callback(data);

            // condition to stop monitoring
            if (data.jobStatus !== "Completed" && data.jobStatus !== "Deleted" && data.numberOfPagesPrinted != data.totalNumberOfPages) {
                ajaxMonitorPrintJob(printerName, jobId, callback, data);
            }
        });
    }
	
    return {
		getLastRunVersion: function () {
			if (!window.localStorage) {
				return null;
			}

			var lastRunVersion = localStorage["printControl.lastRunVersion"];
			if (lastRunVersion == undefined) {
			    return null;
			}
			return lastRunVersion;
		},

        // _pSessionKey, _pSessionValue are optional if the ACC does not require authentication
		init: function (_printControlBaseUrl, _pSessionKey, _pSessionValue, _pFilename, _pURL, _pToken, _pParam, _pCustom, _pCustomHeader, _pCustomParam, _pAlias, _pVerifyString) {
		    if (_printControlBaseUrl == null) {
		        _printControlBaseUrl = "https://localhost:50000/";
		    } else {
		        baseUrl = _printControlBaseUrl;

                // url must end with trailing slash
		        if (baseUrl.match(/\/$/) == null) {
		            baseUrl += "/";
		        }
		    }

            // init ACC params
	        pSessionKey = _pSessionKey;
	        pSessionValue = _pSessionValue;
	        pFilename = _pFilename;
	        pURL = _pURL;
			pToken = _pToken;
			pParam = _pParam;
			pCustom = _pCustom;
			pCustomHeader = _pCustomHeader;
			pCustomParam = _pCustomParam;
			pAlias = _pAlias;
			pVerifyString = _pVerifyString;
		},

		onAjaxStart: function (_onAjaxStart) {
		    $(document).unbind('ajaxStart');
		    $(document).ajaxStart(_onAjaxStart);
		},

		onAjaxStop: function (_onAjaxStop) {
		    if (_onAjaxStop == null) {
		        $(document).trigger('ajaxStop');
		    }

		    $(document).unbind('ajaxStop');
		    $(document).bind('ajaxStop', _onAjaxStop);
		},

		onAjaxError: function (_onAjaxError) {
		    onAjaxError = _onAjaxError;
		},

        // for this to work, user must have at least executed the app once to install the protocol
        // otherwise browser will report unhandled protocol error
		launchApp: function () {
		    var link = $('<a id="printControlLauncher" style="display: none" href="PrintControlProxy:launch"></a>')[0];

            // work around for older versions of IE: add to document and trigger the link
		    document.body.appendChild(link);
		    $('#printControlLauncher')[0].click();

		    document.body.removeChild(link);
	    },

	    ajaxStopPrintControl: function () {
	        return printServiceAjax({
	            url: getBaseUrl() + "stopServer",
                type: "POST"
	        });
	    },

	    ajaxGetPrintControlVersion: function (async) {
	        return ajaxSetup({
	            url: getBaseUrl() + "version"
	        }).done(function (data) {
				if (data.appVersion != undefined)
					setLastRunVersion(data.appVersion);
	        });
	    },
		
		ajaxGetInfo: function (parameters) {
	        return ajaxSetup({
	            url: getBaseUrl() + "info",
				type: "get",
				data: parameters
            }).done(function (data) {
				if (data.appVersion != undefined)
					setLastRunVersion(data.appVersion);
	        });
	    },

	    ajaxPrintTestPage: function (printerName) {
                var printControlVersion = null;
		if (window.localStorage) {
	            lastRunVersion = localStorage["printControl.lastRunVersion"];
		    if (lastRunVersion != undefined)
		        printControlVersion = lastRunVersion;
		}
	        return printServiceAjax({
	            url: getBaseUrl() + "printTestPage",
                type: "POST",
	            data: {
	                printerName: printerName,
	                pSessionKey: pSessionKey,
	                pFilename: pFilename,
	                pURL: pURL,
                    printControlVersion: printControlVersion
	            }
	        });
	    },

		ajaxQueryPrinters: function (refresh, showUnsupported) {
		    return printServiceAjax({
		        url: getBaseUrl() + "queryPrinters",
                        type: "POST",
		        data: {
					refresh: refresh,
		            showUnsupported: showUnsupported,
					pCustom: pCustom,
					pCustomHeader: pCustomHeader,
					pCustomParam: pCustomParam
		        }
		    });
		},
		
		ajaxQueryPrintQueue: function (printerName) {
		    return printServiceAjax({
		        url: getBaseUrl() + "queryPrintQueue",
		        data: {
		            printerName: printerName
		        }
		    });
		},
		
		ajaxQueryPrinterName: function (printerName) {
		    return printServiceAjax({
		        url: getBaseUrl() + "queryPrinterName",
				type: "POST",
		        data: {
		            printerName: printerName,
					pCustom: pCustom
		        }
		    });
		},

		ajaxQueryPrintJobStatus: function (printerName, jobId) {
		    return printServiceAjax({
		        url: getBaseUrl() + "queryJobStatus",
		        data: {
		            printerName: printerName,
                    jobId: jobId
		        }
		    });
		},
		
		ajaxMonitorPrintJob: function (printer, jobId, callback) {
		    ajaxMonitorPrintJob(printer, jobId, callback, null);
		},
		
		ajaxQueryPrintResult: function (filename) {
			console.log("query print result.");
		    return printServiceAjax({
		        url: getBaseUrl() + "queryPrintResult",
				type: "POST",
		        data: {
		            pFilename: filename
		        }
		    });
		},
		ajaxQueryESession: function () {
			
		    return printServiceAjax({
			    url: getBaseUrl() + "queryESession",
			    type: "POST",
				data: {
				    pSessionKey: pSessionKey,
				    pSessionValue: pSessionValue,
                    pFilename: pFilename,
                    pURL: pURL,
                    pToken: pToken,
                    pParam: pParam,
					pCustom: pCustom,
					pCustomHeader: pCustomHeader,
					pCustomParam: pCustomParam,
					pAlias: pAlias,
					pVerifyString: pVerifyString
				},
				isasync: true
			});
		},
		ajaxPrint: function (printerName, pCopiesToPrint) {
		    if (printerName == null || printerName == "") {
		        var msg = "Error: Print Control\nNo printer specified";
		        alert(msg);
                throw new Error(msg);
		    }

		    return printServiceAjax({
			    url: getBaseUrl() + "print",
			    type: "POST",
				data: {
				    pSessionKey: pSessionKey,
				    pSessionValue: pSessionValue,
                    printerName: printerName,
                    pFilename: pFilename,
                    pURL: pURL,
                    pCopiesToPrint: pCopiesToPrint,
                    pToken: pToken,
                    pParam: pParam,
					pCustom: pCustom,
					pCustomHeader: pCustomHeader,
					pCustomParam: pCustomParam,
					pAlias: pAlias,
					pVerifyString: pVerifyString
				}
			});
		}
	}
}();

var PrintServiceUtil = function () {
    return {

        onAjaxStart: function () {
            $('body').css('cursor', 'wait');
        },

        onAjaxStop: function () {
            $('body').css('cursor', 'auto');
        },

        onAjaxError: function (data, textStatus) {
            if (textStatus != "timeout") {
				var msg = "\n";
				if (data.readyState == 0 || data.status == 0) {
					msg += "Could not connect to Print Control";
				} else {
					msg += data.responseText;
				}

				alert('Error: Print Control\n' + msg);
			} else {
				var msg = "\n";
				if (data.readyState == 0 || data.status == 0) {
					msg += "Timeout occured, please try again later.";
				} else {
					msg += data.responseText;
				}

				alert('Error: Print Control\n' + msg);
			}
        },

        // printerList can be retrieved from PrintService.ajaxQueryPrinters()
        renderPrinterListComboBox: function (printerList) {
            var select = $('<select name="printerName" />');

            // add the empty option
            //select.append($('<option />'));

            // populate the list
            for (var i = 0; i < printerList.length; i++) {
                if (printerList[i].isVerified) {
                    var option = $('<option />', {
                        value: printerList[i].printerName,
                        text: printerList[i].printerName
                    });
                }
				else {
                    var option = $('<option />', {
                        value: printerList[i].printerName,
                        text: printerList[i].printerName
                    });
                }
                select.append(option);

                if (printerList[i].isDefault && printerList[i].isSupported) {
                    select.val(printerList[i].printerName);
                }
            }

            return select;
        }
    }
}();