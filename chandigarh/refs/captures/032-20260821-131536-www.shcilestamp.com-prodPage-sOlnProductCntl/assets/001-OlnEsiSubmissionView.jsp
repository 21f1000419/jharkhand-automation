














<!DOCTYPE html>
<html>
    <head>
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Online Stamp Duty Submission</title>
        <link rel="stylesheet" type="text/css" href="/OnlineStamping/libs/OlnCss.css"/>
        <link rel="stylesheet" type="text/css" href="/OnlineStamping/libs/modal.css"/>
        <script type="text/javascript"  src="/OnlineStamping/libs/jquery.js"></script>
        <script src="/OnlineStamping/libs/commonValidation.js" type="text/javascript" ></script>
        <script type="text/javascript" >
        $(document).ready(function()
         {
            $('#trloader').hide();
        });
        function PrintEsiCert(pMsg)
        {
            var vConfirmationFlag;
            var vConfMsg;
            if (pMsg == "PRINT")
            {
                vConfMsg = "Are you sure you want to Print e-Stamping Certificate ?";
            }
            else
            {
                vConfMsg = "Are you sure you want to Re-Print e-Stamping Certificate ?";
            }

            if (confirm(vConfMsg))
            {
                vConfirmationFlag = true;
                $('#trBtnCntl').hide();
                $('#trloader').show();
                $("#frmOlnSubm").submit();
            }
            else
            {
                vConfirmationFlag = false;
                return false;
            }
        }//PrintEsiCert
        
        function RePrintEsiCert(pMsg)
        {
            if (pMsg == "REPRINT")
            {
                $('#spModal').show();
            }            
        }//RePrintEsiCert
        
        function doModalAction(pMsg)
        {
            $('#spModal').hide();
            alert("pMSG "+pMsg);
            if (pMsg == "ACCEPT")
            {
                $('#trBtnCntl').hide();
                $('#trloader').show();
                $("#frmOlnSubm").submit();                
            }
            else
            {
                return false;                
            }
        }//doModalAction
        
        function GenEsiCert()
        {
            var vConfirmationFlag;
            var vConfMsg = "Are you sure you want to Generate this e-Stamping Certificate ?";

            if (confirm(vConfMsg))
            {
                vConfirmationFlag = true;
                $('#trBtnCntl').hide();
                $('#trloader').show();
                $("#frmOlnSubm").submit();
            }
            else
            {
                vConfirmationFlag = false;
                return false;
            }
        }//GenEsiCert

            // CL Code to Call PCP checking.
            function openPost(url,variables)
            {
                //alert(url,variables);
                var form = document.createElement("form");
                form.setAttribute("method", "post");
                form.setAttribute("action", url);
                form.setAttribute("target", "Map");
                for(variable in variables)
                {
                    var hiddenField = document.createElement("input");
                    hiddenField.setAttribute("type", "hidden");
                    hiddenField.setAttribute("name", variable);
                    hiddenField.setAttribute("value", variables[variable]);
                    form.appendChild(hiddenField);
                }

                document.body.appendChild(form);
                var min=window.open('', 'Map','height=400,width=800,status=no,scrollbars=1,toolbar=0,menubar=0,location=0');
                    min.focus();
                    form.submit();
            }//openPost

            function chkPCP()
            {
                var milliseconds = new Date().getTime();
                var vTokens = "-";
                $.ajax({url: "OlnEsiPCPChk.jsp?isid=110235219140_21081314202630108358", data: {'time': milliseconds.toString()}, async: false}).done(function (result)
                {
                    vTokens = $.trim(result);
                });
                var surl="https://www.shcilestamp.com/eStampIndia/selfPrint/SelfPrintServlet";
                if(surl!='null' && surl!=null)
                {
                    var params = [];
                    params["rDoAction"] = 'pcpDownloadRequest';
                    params["token"] = vTokens;
                    openPost(surl,params);
                }
            }//window.onload

        </script>
    </head>
    <body>
        <div id="formWrapper">
        <div class="wrap-message-box" ><span class="wrap-message">Please wait , Processing....</span></div>
        <form name="frmOlnSubm" id="frmOlnSubm" method="post" action="../sOlnEsiPrintCert" >
            <h1 class="fHeader">:: Online Stamp Duty Submission View</h1>
            <hr style=" margin-bottom:10px" />
            <div style="margin:5px 5px 5px 5px;" id="tabRcpt">
                <center><h1 style="padding-bottom: 5px; font-family: Arial, Helvetica, sans-serif; font-size: 18px; color: #666666; font-weight: bold">Payment Receipt</h1></center>
                    
                <input type="hidden" name="iUsrCd" id="iUsrCd" value="compitcom">
                <input type="hidden" name="isid" id="isid" value="110235219140_21081314202630108358">
                <input type="hidden" name="iRefNo" id="iRefNo" value="null">
            </div>
            <center>
                <table width="100%">


                <tr valign="center" align="center" id="trBtnCntl"><td>
                <input type="button" name="btnPrint" id="btnPrint" value="Print Payment Receipt" class="fSubmit" onclick="tablePrintBig(tabRcpt,'/OnlineStamping');">


        </td></tr>
        <!--for spin animation-->
        <tr valign="center" align="center" id="trloader" >
            <td style="align-content: center">
                <div class="loader" style="text-align:center;vertical-align:central;width:64px;height:50px;background:url('/OnlineStamping/images/loading1.gif') 50% 50% no-repeat transparent ;opacity: 0.8;">
                </div>
            </td>
        </tr>
        </table>
        </center>
        </form>
        </div>
        <!-- The Modal -->
        <div id="spModal" class="modal">
          <!-- Modal content -->
          <div class="modal-content">
              <span id="clsModal" class="close" onclick="doModalAction('CLOSE');">&times;</span>
            <p>Are you sure you want to Re-Print e-Stamping Certificate ?</p>
            <br>
            <input type="button" name="btnAcceptModal" id="btnAcceptModal" value="Accept" class="fSubmit" onclick="doModalAction('ACCEPT');">
            <input type="button" name="btnCancelModal" id="btnCancelModal" value="Close" class="fSubmit" onclick="doModalAction('CANCEL');">
          </div>
        </div>                
    </body>
</html>
