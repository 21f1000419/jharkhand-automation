







<!DOCTYPE html>
<html>
<head>
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    <link rel="stylesheet" type="text/css" href="../libs/OlnCss.css"/>
    <script src="../libs/commonValidation.js" type="text/javascript" ></script>
    <script src="../libs/jquery.js" type="text/javascript" ></script>
    <script src="../libs/jquery.validate.js" type="text/javascript" ></script>
    <script type="text/javascript" lang="javascript" >

        function StateSelect()
        {
            var vSttCd = document.getElementById("iSttCd").value;
            $("#tr_Bnk").hide();
            var milliseconds = new Date().getTime();
            $.ajax({ url: "OlnEsiGetNRBankDtls.jsp?iUsr=compitcom&uProdCd=ESI&uSttCd="+vSttCd, data:{ 'time':milliseconds.toString() }}).done(function(result)
            {
                    $('#BnkDiv').html('').html($.trim(result));
                    $("#tr_Bnk").show();
            });
            return true;
        }//StateSelect

    </script>
</head>
<body>
<table style="margin-bottom: 5px; text-align: left; width: 95%"  >
<tr>
    <td class="fHeader">:: Online e-Stamp Duty Payment</td>
</tr>
<tr>
    <td align="center"><hr style=" margin-bottom: 10px;"></td>
</tr>

<tr><td align="center">
<div class="NewsInfoBox">
Welcome to e-Stamping Fee Payment Online Module
</div></td></tr>

<tr><td align="center">
<div class="NewsInfoBox">
Single e-Stamp certificate will be issued for single Net Banking/Debit Card/NEFT/RTGS Payment.
</div></td></tr>

<tr><td align="center">
<div class="NewsInfoBox">
Self Printing Option is available for Delhi upto Rs 10,000/-, Chandigarh and Pondicherry  for payment of stamp duty upto Rs 500/-, for Himachal Pradesh and Laddakh upto Rs. 5000/-, for Karnataka state for Articles 4 - Affidavit and Articles 5(J) - Agreement (In any other cases), Jammu and Kashmir and Andaman and Nicobar for payment of stamp duty upto Rs.1000/-.
</div></td></tr>

<tr><td align="center">
<div class="NewsInfoBox">
<b>For Uttar Pradesh stamp duty can be paid up to Rs. 500/- through Self Printing Mode.</b>
</div></td></tr>

<tr style="margin-bottom: 5px " ><td align="center">
<div class="NewsInfoBox">
A typical user may use following options :
<br>1. Create Stampduty Submission.
<br>2. Generate Online Payment Receipts by making payment through NEFT/RTGS/Net Banking/Debit Card/Cash at StockHolding Counter.
<br>3. View Transaction Reports
</div></td></tr>
<!--tr style="margin-bottom: 5px " ><td align="center">
<div class="NewsInfoBox">
Please find out your nearest
<span style="color: #990000" ><a onclick="javascript:window.open('http://online.stockholding.com/aboutus/shcil_contactus.aspx','','scrollbars=yes,taskbar=no,resizable=no,titlebar=no,width=800,height=600,top=50,left=50');" onmouseout="document.body.style.cursor='auto'" onmouseover="document.body.style.cursor='pointer'"><u>StockHolding Branch Here.</u></a></span>
</div></td></tr-->
<tr><td align="center">

</td></tr>
<!-- tr>
    <td align="left">
    <div>
    <div class="fHeaderSub" style=" color: #ff0000;margin-top: 10px; margin-bottom: 5px" >
    StockHolding Accounts Details for NEFT/RTGS Payments
    </div>
        <span class="fItemText" >Select State <span style="color: #ff0000"> *</span></span>
    <select name="iSttCd"  id="iSttCd" style="width:30%" class="fItemInputBox" onchange="return StateSelect()" >
    <option selected="selected" label="Select State" value="0" >Select State </option>
            <!--%
                    ArrayList<String[]> sttLst = stLst.GetStateList(vUsr,"Y",vPID);
                    int sttCnt = 0;
                    System.out.println("SIZE OF ARRAYLIST : "+sttLst.size()+" / "+sttLst.get(sttCnt)[0]);
                    while(sttCnt < sttLst.size())
                    {
            %>
                        <option label="<!--%=sttLst.get(sttCnt)[0]%>" value="<!--%=sttLst.get(sttCnt)[1]%>" ><!--%=sttLst.get(sttCnt)[0]%></option>
            <!--%
                        sttCnt = sttCnt + 1;
                    }//while
            %>
                </select>
    </div>
    </td>
</tr>
    <tr id="tr_Bnk" style="display:none" >
        <td align="left">
            <div id="BnkDiv" class="NewsInfoBox">

            </div>
        </td>
</tr !-->
</table>
</body>
</html>
