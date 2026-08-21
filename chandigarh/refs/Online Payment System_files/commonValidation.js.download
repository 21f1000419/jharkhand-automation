//-------------------------------------------------------------------------------------------------------------------------------------------------
//Desaible Right Click
history.go(1);



var message="Function Disabled!";
function clickIE4()
{
    if (event.button==2)
    {
        //alert(message);
        return false;
    }
}

function clickNS4(e)
{
    if (document.layers||document.getElementById&&!document.all)
    {
        if (e.which==2||e.which==3)
        {
            //alert(message);
            return false;
        }
    }
}

if (document.layers)
{
    document.captureEvents(Event.MOUSEDOWN);
    document.onmousedown=clickNS4;
}
else if (document.all && !document.getElementById)
{
    document.onmousedown=clickIE4;
}
//document.oncontextmenu=new Function("alert(message);return false")
document.oncontextmenu=new Function("return false;") ;

function chkkeyfunction(netscape)
{
    var keyPressed=(netscape||event).keyCode;

    if(keyPressed==116)
    {
        alert("This action is not allowed.");
        if(!netscape){event.keyCode=0;}
        return false;
    }
    return true;
}//disableRefresh

document.onkeydown=chkkeyfunction;


function checkClient(vPopUpURL)
{
    var display_setting="width=1,height=1,left=0,top=0,menubar=0,scrollbars=0,status=0,toolbar=0,resizable=0,dialog=0";
    var winName = "userConsole";
    //var popUp = window.open(vPopUpURL,winName,display_setting);

    /*
    if (popUp == null || typeof(popUp)=='undefined')
    {
	alert('Please disable your pop-up blocker, Or Allow pop-ups for this site.');
    }
    */
    if(!navigator.javaEnabled())
    {
        alert('Enable Java in your Browser.\n Kindly note that the Online Payment System requires minimum JRE 1.6 and above version...');
    }
}//checkClient

//-------------------------------------------------------------------------------------------------------------------------------------------------
function tablePrint(tabObj)
{
    var display_setting="toolbar=yes,location=no,directories=yes,menubar=yes,";
    display_setting+="scrollbars=yes,width=500,height=500 left=10, top=10s";

    var content_innerhtml = tabObj.innerHTML;
    var document_print=window.open("PrntWindow","",display_setting);
    document_print.document.open();
    document_print.document.write('<html><head><title>PrintWindow</title><link rel="stylesheet" type="text/css" href="../libs/OlnCss.css" /></head>');
    document_print.document.write('<body onLoad="self.print();" style="margin-top:10;margin-left:15;margin-right:15;margin-bottom:15" >');
    document_print.document.write('<div align=left><img src="../images/shcillogo.gif" height="50" widht="50" ></div>');
    document_print.document.write(content_innerhtml);
    document_print.document.write('<div align=left><font face="arial" size=2 color="darkblue">This is a system generated receipt.</font></div><br>');
    //document_print.document.write('<div align=right><font face="arial" size=2 color="darkblue">&copy;&nbsp;Stock Holding Corporation of India Ltd.</font></div>');
    document_print.document.write('</body></html>');
    //document_print.print();
    document_print.document.close();
    return false;
}//tablePrint


function tablePrintBig(tabObj,vDomain)
{
    var display_setting="toolbar=yes,location=no,directories=yes,menubar=yes,";
    display_setting+="scrollbars=yes,width=800,height=600 left=10, top=10s";

    var content_innerhtml = tabObj.innerHTML;
    var document_print=window.open("PrntWindow","",display_setting);
    document_print.document.open();
    document_print.document.write('<html><head><title>PrintWindow</title><link rel="stylesheet" type="text/css" href="'+vDomain+'/libs/OlnCss.css" /></head>');
    document_print.document.write('<body onLoad="self.print();" style="margin-top:10;margin-left:15;margin-right:15;margin-bottom:15" >');
    document_print.document.write('<div align=left><img src="'+vDomain+'/images/shcillogo.gif" alt="" height="50" widht="50" ></div>');
    document_print.document.write(content_innerhtml);
    document_print.document.write('<div align=left><font face="arial" size=2 color="darkblue">This is a system generated receipt.</font></div><br>');
    //document_print.document.write('<div align=right><font face="arial" size=2 color="darkblue">&copy;&nbsp;Stock Holding Corporation of India Ltd.</font></div>');
    document_print.document.write('</body></html>');
    //document_print.print();
    document_print.document.close();
    return false;
}//tablePrintBig


function tablePrintServlet(tabObj)
{
    var display_setting="toolbar=yes,location=no,directories=yes,menubar=yes,";
    display_setting+="scrollbars=yes,width=500,height=500 left=10, top=10s";

    var content_innerhtml = tabObj.innerHTML;
    var document_print=window.open("PrntWindow","",display_setting);
    document_print.document.open();
    document_print.document.write('<html><head><title>PrintWindow</title><link rel="stylesheet" type="text/css" href="libs/OlnCss.css" /></head>');
    document_print.document.write('<body onLoad="self.print();" style="margin-top:10;margin-left:15;margin-right:15;margin-bottom:15" >');
    document_print.document.write('<div align=left><img src="images/shcillogo.gif" height="50" widht="50" ></div>');
    document_print.document.write(content_innerhtml);
    document_print.document.write('<div align=left><font face="arial" size=2 color="darkblue">This is a system generated receipt.</font></div><br>');
    //document_print.document.write('<div align=right><font face="arial" size=2 color="darkblue">&copy;&nbsp;Stock Holding Corporation of India Ltd.</font></div>');
    document_print.document.write('</body></html>');
    //document_print.print();
    document_print.document.close();
    return false;
}//tablePrint


function validatePan(field)
{
    var pan = /^([A-Z]{5})+([0-9]{4})+([A-Z]{1})$/;
    if (field.value != "")
    {
        if (!(field.value.match(pan)))
        {
            alert("PAN no. should be in correct format") ;
            field.focus();
            return false ;
        }
    }
    return true;
}//validatePan

function checkInstrnumber(obj)
{
	var x=obj.value;
	//var vInstType =  document.getElementById('<%=lstInstType.ClientID%>')[document.getElementById('<%=lstInstType.ClientID%>').selectedIndex].value;
        var vInstType =  'CHEQUE';

	if(vInstType =='RTGS' || vInstType =='NEFT' || vInstType =='NETBANKING')
        {
			var valid = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
			var temp;
			for (var i=0; i<obj.value.length; i++){
					temp = "" + obj.value.substring(i, i+1);
					if (valid.indexOf(temp) == "-1"){
							obj.value = obj.value.substring(0, i);
							//document.getElementById("instrNum").value = '';
							return true;
							//break;
					}
			}
	  if(vInstType =='RTGS' && x.length < 16 || x.length > 22 ){
			alert('UTR Number length is minmum 16 characters and maximum 22 characters. Please enter again.');
			return true;
		}
	  if(vInstType =='NEFT' && x.length < 16 || x.length > 22 ){
			alert('Instrument Number length is minmum 16 characters and maximum 22 characters. Please enter again.');
			return true;
          }
		return false;
	}
        // for Instrument Type CHEQUE and DD
	else
        {
		if(x != null && x != "")
		{
			var anum="";
			if(x.indexOf(".")!=-1 && x.indexOf(".")!=0 )
				anum=/(^\d+$)|(^\d+\.\d+$)/
			else if(x.indexOf(".")==0 )
			{
				x = "0" + x;
				//alert(x);
				anum=/(^\d+$)|(^\d+\.\d+$)/
			}
			else
				anum=/(^\d+$)/

			if (anum.test(x))
				return false;
			else
			{
				obj.value ="";
				//document.getElementById('<%=lstInstType.ClientID%>').focus();
                                obj.focus();
				alert('Please type numeric data.');
				return true;
			}
		}
                return false;
	}
}//checkInstrnumber

function checkWithSysDate(obj)
{

    if (obj.value!="" ||obj.value!=null)
    {
        var xDate = obj.value;
        var cDate = new Date();

        if(Date.parse(formatDate(xDate)) > cDate)
        {
                alert("Entered Date ["+xDate+"] cannot be greater than Todays Date, Please enter a valid date.");
                obj.focus();
                return false;
        }
    }
    return true;
}//checkWithSysDate

function checkInstDate(obj)
{
    //alert("checkInstDate " + obj.value);
    if (obj.value!="" ||obj.value!=null)
    {
        var xDate = obj.value;
        var cDate = new Date();
        var l3Date = new Date();
        l3Date.setMonth(new Date().getMonth() -3);
        //alert(xDate+" / "+cDate+" / "+l3Date);

        if(Date.parse(formatDate(xDate)) > cDate)
        {
                alert("Instrument Date ["+xDate+"] cannot be greater than Todays Date, Please enter a valid Instrument date.");
                obj.focus();
                return false;
        }
        if(Date.parse(formatDate(xDate)) < l3Date)
        {
                alert("Instrument Date ["+xDate+"] cannot be less than last three months, Please enter a valid Instrument date.");
                obj.focus();
                return false;
        }
    }
    return true;
}//checkInstDate

function validatePin(field)
{
    var valid = "0123456789";
	var apin = field.value;
    if(apin !="")
	{
    	if(apin.length != 6)
        {
		    alert("Invalid Pin number length! Please try again.")
		    field.focus();
		    return false;
	    }
        for (var i=0; i < 6; i++)
        {
		    temp = "" + apin.substring(i, i+1);
		    if (valid.indexOf(temp) == "-1")
		    {
			    alert("Invalid characters in your pin. Please try again.")
			    field.focus();
			    return false;
		    }
	    }
	}
	return true;
}//validatePin

function checkMailID(field)
{
    var filter = /^([a-zA-Z0-9_\.\-])+\@(([a-zA-Z0-9\-])+\.)+([a-zA-Z0-9]{2,4})+$/;
    if (field.value != "")
    {
        if (!filter.test(field.value))
        {
            alert('Please provide a valid email address');
            field.focus();
            return false;
        }
    }
    return true;
}//checkMailID

function formatDate(value)
{
    var day = value.substring ( 0, value.indexOf ("-") );
    var month = value.substring ( value.indexOf ("-")+1, value.lastIndexOf  ("-") );
    var year = value.substring ( value.lastIndexOf ("-")+1, value.length );
    return month+" "+day+" "+year;
}//formatDate

function checkValidDate(fld)
{
    if (fld.value=="")
        return true;
    if(fld.value != "" || fld.value != null)
    {
            var theString = new String(fld.value);
            var delimiterCharacter="-";
            var ok = true;
            var frmt = true;
            var leap = false;
            var isMonth = false;
            monthArr = new Array ("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC");
            dateArr = new Array (31,28,31,30,31,30,31,31,30,31,30,31);
            dateLeapArr = new Array (31,29,31,30,31,30,31,31,30,31,30,31);

            if (theString.length != 11)
            {
                    frmt = false;
            }
            else
            {
                    if (theString.charAt(2) != "-" && theString.charAt(6) != "-")
                    {
                            frmt = false;
                    }
                    else
                    {
                            var tokens = theString.split(delimiterCharacter);
                            if (tokens.length!=3)
                            {
                                    frmt = false;
                            }
                            else
                            {
                                    var date = tokens[0];
                                    var month = tokens[1].toUpperCase();
                                    var year = tokens[2];
                                    if (isNaN(year) || isNaN(date))
                                    {
                                            ok = false;
                                    }
                                    else
                                    {
                                            if (year%4==0)
                                            {
                                                    if (year%100==0)
                                                    {
                                                            if (year%400==0)
                                                                    leap=true;
                                                    }
                                                    else
                                                    {
                                                            leap = true;
                                                    }
                                            }
                                            for (var i=0; i<12; i++)
                                            {
                                                    if (month==monthArr[i])
                                                    {
                                                            if (leap)
                                                            {
                                                                    if (date>dateLeapArr[i])
                                                                    ok = false;
                                                            }
                                                            else
                                                            {
                                                                    if (date>dateArr[i])
                                                                    ok = false;
                                                            }
                                                            isMonth = true;
                                                            break;
                                                    }
                                            }
                                            if (!isMonth)
                                                    ok = false;
                                    }
                            }
                    }
            }
            if (!ok)
            {
                    alert("Entered Date is not sValid,Kindly Check");
                    fld.focus();
                    return false;
            }
            if (!frmt)
            {
                    alert("Entered Date is not in Valid Format,Kindly Check");
                    fld.focus();
                    return false;
            }
           return true;
    }
    return true;
}//checkValidDate

function checkEmpty(field)
{
	var userInput;
	var iStart, iEnd;
	var sTrimmed;
	var cChar;
	var ok = "Y";

        //alert("checkEmpty 1");
	userInput = field.value;
	iEnd = userInput.length - 1;
	iStart = 0;
	bLoop = true;

	if(userInput == "" || userInput == " ")
		ok = "N";
        //alert("checkEmpty 2");
	cChar = userInput.charAt(iStart);
	while ((iStart < iEnd) && ((cChar == "\n") || (cChar == "\r") || (cChar == "\t") || (cChar == " ")))
	{
		ok = "N";
		iStart ++;
		cChar = userInput.charAt(iStart);
	}
        //alert("checkEmpty 3");
	cChar = userInput.charAt(iEnd);
	while ((iEnd >= 0) && ((cChar == "\n") || (cChar == "\r") || (cChar == "\t") || (cChar == " ")))
	{
		iEnd --;
		cChar = userInput.charAt(iEnd);
	}
        //alert("checkEmpty 4");
	if (iStart < iEnd)
	{
		sTrimmed = userInput.substring(iStart, iEnd + 1);
	}
	else
	{
		sTrimmed = "";
	}

	//alert("Untrimmed string is \"" + field.value + "\"\nTrimmed string is \"" + sTrimmed + "\"");
	if(ok != "Y")
	{
		//alert("Please Enter a valid String without spaces in the beginning");
		field.focus();
		//field.select();
		return false;
	}
	return true;
}//checkEmpty

function validateNull(field)
{
    var vStr = field.value
    if (vStr == "")
    {
        alert("This field is mandatory.Kindly Enter.");
        field.focus();
    }
} // validateNull

function validateTextarea(field)
{
    if (field.value != "")
    {
        var valid = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,?:;!@#$()+*-/ ";
        var temp;
        var ok = "yes";
        var nums = "0123456789";
        var onlyNum = "yes"

        for (var i=0; i<field.value.length; i++){
                temp = "" + field.value.substring(i, i+1);
                if (valid.indexOf(temp) == "-1"){
                        ok = "no";
						field.value = field.value.substring(0, i);
                        break;
                }
        }

        for (var i=0; i<field.value.length; i++){
                temp = "" + field.value.substring(i, i+1);
                if (nums.indexOf(temp) == "-1")
                {
                    onlyNum = "no";
					//field.value = field.value.substring(0, i);
                    break;
                }
            }

        if (ok == "no") {
				//alert("Invalid Entry!. Special character ' " + temp + " ' is not allowed.");
				alert('You have provided an invalid Entry. Special character '+temp+' is not allowed.');
		   		field.focus();
				//field.select();
				return true;
        }
        else if (onlyNum == "yes")
        {
		        alert('You have only numbers. Only Numbers are not allowed.');
		   		field.focus();
				//field.select();
				return true;
        }
        else
        {
		    return false;
		}
	}
}//validateTextarea

function textCounter(field,maxlimit) {
     	   if (field.value.length+1 > maxlimit) // if too long...trim it!
     	   {
		   alert('Maximum [' + maxlimit + '] characters are allowed in this Field.');
		   event.keyCode=0;
     	   field.value = field.value.substring(0, maxlimit);
     	   field.focus();
     	   return false;
     	   }
     	   else return true;
}//textCounter

function replaceEnterKey(textarea,replaceWith)
{
				textarea.value=escape(textarea.value)
				for(i=0; i<textarea.value.length; i++){
 				if(textarea.value.indexOf("%0D%0A") > -1){
					textarea.value=textarea.value.replace("%0D%0A",replaceWith)
				}
				else if(textarea.value.indexOf("%0A") > -1){
					textarea.value=textarea.value.replace("%0A",replaceWith)
				}
				else if(textarea.value.indexOf("%0D") > -1){
					textarea.value=textarea.value.replace("%0D",replaceWith)
				}
				}
			 textarea.value=unescape(textarea.value)
}//replaceEnterKey

function validateAlpha(field)
{
        var valid = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ ";
        var temp;
        var ok = "yes";
        for (var i=0; i<field.value.length; i++){
                temp = "" + field.value.substring(i, i+1);
                if (valid.indexOf(temp) == "-1"){
                        ok = "no";
						field.value = field.value.substring(0, i);
                        break;
                }
        }
        if (ok == "no") {
				alert('You have provided an invalid Entry. Character '+temp+' is not allowed.');
		   		field.focus();
				//field.select();
				return true;
        } else {
				return false;
			}
} // validateAlpha

function validateAlphaNum(field)
{
        if (field.value != "")
        {
            var valid = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_";
            var nums = "0123456789";
            var temp;
            var ok = "yes";
            var onlyNum = "yes"
            for (var i=0; i<field.value.length; i++){
                    temp = "" + field.value.substring(i, i+1);
                    if (valid.indexOf(temp) == "-1")
                    {
                            ok = "no";
						    field.value = field.value.substring(0, i);
                            break;
                    }
            }
            for (var i=0; i<field.value.length; i++){
                    temp = "" + field.value.substring(i, i+1);
                    if (nums.indexOf(temp) == "-1")
                    {
                            onlyNum = "no";
						    //field.value = field.value.substring(0, i);
                            break;
                    }
            }
            if (ok == "no")
            {
				    alert('You have provided an invalid Entry. Special character '+temp+' is not allowed.');
		   		    field.focus();
				    //field.select();
				    return true;
            } else if (onlyNum=="yes")
            {
				    alert('You have only numbers. Only Numbers are not allowed.');
		   		    field.focus();
				    //field.select();
				    return true;
            }
            else
            {
				    return false;
		    }
        }
}//validateAlphaNum


function validatePartyName(field)
{
        if (field.value != "")
        {
            var valid = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ";
            var nums = "0123456789";
            var temp;
            var ok = "yes";
            var onlyNum = "yes"
            for (var i=0; i<field.value.length; i++){
                    temp = "" + field.value.substring(i, i+1);
                    if (valid.indexOf(temp) == "-1")
                    {
                            ok = "no";
						    field.value = field.value.substring(0, i);
                            break;
                    }
            }
            for (var i=0; i<field.value.length; i++){
                    temp = "" + field.value.substring(i, i+1);
                    if (nums.indexOf(temp) == "-1")
                    {
                            onlyNum = "no";
						    //field.value = field.value.substring(0, i);
                            break;
                    }
            }
            if (ok == "no")
            {
				    alert('You have provided an invalid Entry. Special character '+temp+' is not allowed.');
		   		    field.focus();
				    //field.select();
				    return true;
            } else if (onlyNum=="yes")
            {
				    alert('You have only numbers. Only Numbers are not allowed.');
		   		    field.focus();
				    //field.select();
				    return true;
            }
            else
            {
				    return false;
		    }
        }
}//validatePartyName

function validateAlphaNumCAPS(field)
{
        if (field.value != "")
        {
            var valid = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
            var temp;
            var ok = "yes";
            for (var i=0; i<field.value.length; i++){
                    temp = "" + field.value.substring(i, i+1);
                    if (valid.indexOf(temp) == "-1")
                    {
                            ok = "no";
						    field.value = field.value.substring(0, i);
                            break;
                    }
            }
            if (ok == "no")
            {
				    alert('You have provided an invalid Entry. Special character '+temp+' is not allowed.');
		   		    field.focus();
				    //field.select();
				    return true;
            }
            else
            {
				    return false;
	    }
            return true;
        }
}//validateAlphaNum

// numeric data and greater than zero
function checkNumber(obj)
{
	var x=obj.value
	if(x != null && x != "")
	{
	    if (x <= 0)
	    {
            alert('Please type Amount greater than zero.');
			obj.value ="";
			obj.focus();
			return true;
	    }

		var anum="";
		if(x.indexOf(".")!=-1 && x.indexOf(".")!=0 )
			anum=/(^\d+$)|(^\d+\.\d+$)/
		else if(x.indexOf(".")==0 )
		{
			x = "0" + x;
			//alert(x);
			anum=/(^\d+$)|(^\d+\.\d+$)/
		}
		else
			anum=/(^\d+$)/

		if (anum.test(x))
			return false;
		else
		{
			//alert("Please input a valid number!");
			alert('Please type numeric data.');
			obj.value ="";
			obj.focus();
			return true;
		}
	}
}//checkNumber

// numeric data and greater than zero
function checkNumber1(obj)
{
	var x=obj.value
	if(x != null && x != "")
	{
	    if (x < 0)
	    {
            alert('Please type Amount greater than zero.');
			obj.value ="0";
			obj.focus();
			return true;
	    }

		var anum="";
		if(x.indexOf(".")!=-1 && x.indexOf(".")!=0 )
			anum=/(^\d+$)|(^\d+\.\d+$)/
		else if(x.indexOf(".")==0 )
		{
			x = "0" + x;
			//alert(x);
			anum=/(^\d+$)|(^\d+\.\d+$)/
		}
		else
			anum=/(^\d+$)/

		if (anum.test(x))
			//return false;
                            return true;
		else
		{
			//alert("Please input a valid number!");
			alert('Please type numeric data.');
			obj.value ="0";
			obj.focus();
			return true;
		}
	}
}//checkNumber

function checkPhNumber(field)
{

    var valid = "0123456789";
	var aphone = field.value;
    if(aphone !="")
	{
    	if(aphone.length < 10 || aphone.length > 13)
        {
		    alert("Invalid phone number length! Please try again.")
		    field.focus();
		    return false;
	    }
        for (var i=0; i < aphone.length; i++)
        {
		    temp = "" + aphone.substring(i, i+1);
		    if (valid.indexOf(temp) == "-1")
		    {
			    alert("Invalid characters in your phone. Please try again.")
			    field.focus();
			    return false;
		    }
	    }
	}
	return true;
}//checkPhNumber

// removes decimal point & digits after point
function checkForDecimalPlaces(obj)
{
	if("" != trimAll(obj.value))
	{
		if(parseFloat(obj.value) <= 999999999999999.00)
		{
			obj.value = roundNumber(obj.value, 0, true);
		}
		else
		{
			var val = obj.value;
			val = val.substring(0, 15);
			obj.value = val;
			alert('The value is too large to be accepted. \nResetting the value length to maximum permitted length of [15] digits'+'\n['+val+']');
		}
	}
}//checkForDecimalPlaces

function trimAll(sString)
{
    while (sString.substring(0,1) == ' ')
    {
        sString = sString.substring(1, sString.length);
    }
    while (sString.substring(sString.length-1, sString.length) == ' ')
    {
        sString = sString.substring(0,sString.length-1);
    }
    return sString;
}//trimall

function roundNumber(num, ndec, addz)
{
     if (num != "")
     {
          var factor = Math.pow(10, ndec);

          num = Math.round(num * factor);
          num = num / factor;
          if (addz)
          {
               var dot = ("" + num).indexOf(".");
               if (dot>=0)
               {
                    var nzeros = dot + ndec + 1 - ("" + num).length;
                    for (i=0; i< nzeros; i++)
                    {
                         num = num + "0";
                    }
               }
               else
               {
                    num = num;
                    for (i=0; i< ndec; i++)
                    {
                         num = num + "0";
                    }
               }
          }
     }
     return (num);
}//roundNumber

function roundNumber_dec(num, dec)
{
	var result = Math.round(num*Math.pow(10,dec))/Math.pow(10,dec);
        //alert (" roundNumber(num, dec) ");
	return result;
}

// round to two places of decimal point
function roundNumber(obj)
{
        var result = Math.round(obj.value * Math.pow(10,2))/Math.pow(10,2);
	//alert (" obj");
        obj.value = result;
        return true;
}

// Convert numbers to words
function NumtoWords(Obj)
{
// American Numbering System
var th = ['','Thousand','Million', 'Billion','Trillion'];
// uncomment this line for English Number System
// var th = ['','thousand','million', 'milliard','billion'];

var dg = ['Zero','One','Two','Three','Four', 'Five','Six','Seven','Eight','Nine'];
var tn = ['Ten','Eleven','Twelve','Thirteen', 'Fourteen','Fifteen','Sixteen', 'Seventeen','Eighteen','Nineteen'];
var tw = ['Twenty','Thirty','Forty','Fifty', 'Sixty','Seventy','Eighty','Ninety'];
var s = Obj.value;
s = s.toString();
s = s.replace(/[\, ]/g,'');
if (s != parseFloat(s))
return 'not a number';
var x = s.indexOf('.');
if (x == -1)
	x = s.length;
if (x > 15)
	return 'too big';

var n = s.split('');
var str = '';
var sk = 0;
for (var i=0; i < x; i++)
{
	if ((x-i)%3==2)
	{
		if (n[i] == '1')
		{
			str += tn[Number(n[i+1])] + ' ';i++;sk=1;
		}
		else if (n[i]!=0)
		{
			str += tw[n[i]-2] + ' ';sk=1;
		}
	} else if (n[i]!=0)
	{
		str += dg[n[i]] +' ';
		if ((x-i)%3==0)
			str += 'hundred ';sk=1;
	}if ((x-i)%3==1)
	{
		if (sk)
			str += th[(x-i-1)/3] + ' ';sk=0;
	}
}
if (x != s.length)
{
	var y = s.length;
	str += 'Rupees and Paise ';
	for (var i=x+1; i<y; i++)
		str += dg[n[i]] +' ';
}
    alert(str.replace(/\s+/g,' '));
    return false;
}//NumtoWords

function CheckLoginDetails(uID,uPass,SecTxt,iTxtVrf)
{
    var usr = uID.value;
    var pass = uPass.value;
    var vSectxt = SecTxt.value;
    var vTxtVrf = iTxtVrf.value;

    if (usr == "" || usr == null)
    {
        alert("User ID is mandatory.Kindly Enter.");
        uID.focus();
        return false;
    }
    if (pass == "" || pass == null)
    {
        alert("Password is mandatory.Kindly Enter.");
        uPass.focus();
        return false;
    }

    if (vTxtVrf == "" || vTxtVrf == null)
    {
        alert("Verification Text is mandatory.Kindly Enter.");
        iTxtVrf.focus();
        return false;
    }

    if(vTxtVrf != vSectxt)
    {
        alert("Verification Text is not matching.Kindly Re-Enter.");
        iTxtVrf.value="";
        iTxtVrf.focus();
        return false;
    }
    document.forms["loginForm"].submit();
    return true;
}//CheckLoginDetails

function convertNumberToWords(numVal)
{
	var junkVal=numVal.value;
    junkVal=Math.floor(junkVal);
    var obStr=new String(junkVal);
    numReversed=obStr.split("");
    actnumber=numReversed.reverse();

    if(Number(junkVal) >=0)
    {
        //do nothing
    }
    else
    {
        alert('Invalid Number');
        return false;
    }
    if(Number(junkVal)==0)
    {
       // document.getElementById('container').innerHTML=obStr+''+'Rupees Zero Only';
        return false;
    }
    if(actnumber.length>12)
    {
        alert('The number conversion is allowed only to the length of 12 characters.');
        return false;
    }

    var iWords=["Zero", " One", " Two", " Three", " Four", " Five", " Six", " Seven", " Eight", " Nine"];
    var ePlace=['Ten', ' Eleven', ' Twelve', ' Thirteen', ' Fourteen', ' Fifteen', ' Sixteen', ' Seventeen', ' Eighteen', ' Nineteen'];
    var tensPlace=['dummy', ' Ten', ' Twenty', ' Thirty', ' Forty', ' Fifty', ' Sixty', ' Seventy', ' Eighty', ' Ninety' ];

    var iWordsLength=numReversed.length;
    var totalWords="";
    var inWords=new Array();
    var finalWord="";
	var currency="";
    j=0;
    for(i=0; i<iWordsLength; i++)
    {

        switch(i)
        {
        case 0:
            if(actnumber[i]==0 || actnumber[i+1]==1 )
            {
                inWords[j]='';
            }
            else
            {
                inWords[j]=iWords[actnumber[i]];
            }
            inWords[j]=inWords[j];
            break;
        case 1:
            tens_complication();
            break;
        case 2:
            hundred_complication();
            break;
        case 3:
			thousand_complication();
            break;
        case 4:
            tens_complication();
            break;
        case 5:
			if(actnumber[i]==0 && actnumber[i+1]==0 && (actnumber[i+2] > 0 || actnumber[i+3] > 0 || actnumber[i+4] > 0|| actnumber[i+5] >= 0))
            {
                inWords[j]= '';
            }
			else if(actnumber[i]==0 || actnumber[i+1]==1 )
			{
				inWords[j]= " Lakh";
			}
            else
            {
                inWords[j]=iWords[actnumber[i]]+" Lakh";
            }

            break;
        case 6:
            tens_complication();
            break;
        case 7:
            if(actnumber[i]==0 || actnumber[i+1]==1 )
            {
                inWords[j]='';
            }
            else
            {
                inWords[j]=iWords[actnumber[i]];
            }
            inWords[j]=inWords[j]+" Crore";
            break;
        case 8:
            tens_complication();
            break;
		case 9:
			hundred_complication();
			break;
		case 10:
			thousand_complication();
			break;
		case 11:
            tens_complication();
            break;
        default:
            break;
        }
        j++;
    }

    function tens_complication()
    {
        if(actnumber[i]==0)
        {
            inWords[j]='';
        }
        else if(actnumber[i]==1)
        {
            inWords[j]=ePlace[actnumber[i-1]];
        }
        else
        {
            inWords[j]=tensPlace[actnumber[i]];
        }
    }

	function hundred_complication()
	{
		 if(actnumber[i]==0)
            {
                inWords[j]='';
            }
            else if(actnumber[i-1]!=0 || (actnumber[i]!=0 && actnumber[i-2]!=0))
            {
                inWords[j]=iWords[actnumber[i]]+' Hundred and ';
            }
            else
            {
                inWords[j]=iWords[actnumber[i]]+' Hundred ';
            }
	}

	function thousand_complication()
	{
		if(actnumber[i]==0 && actnumber[i+1]==0 && (actnumber[i+2] > 0 || actnumber[i+3] > 0 || actnumber[i+4] > 0 || actnumber[i+5] > 0 || actnumber[i+6] > 0 || actnumber[i+7] >= 0))
			{
				inWords[j]='';
			}
			else if(actnumber[i]==0 || actnumber[i+1]==1)
			{
			    inWords[j]=" Thousand ";
			}
			else
			{
				inWords[j]=iWords[actnumber[i]]+" Thousand ";
			}
	}

    inWords.reverse();
    for(i=0; i<inWords.length; i++)
    {
        finalWord+=inWords[i];
    }

	if(finalWord == " One")
		currency = " Rupee";
	else
		currency = " Rupees";
	alert(finalWord+currency);
    //document.getElementById('container').innerHTML=finalWord;
    //return true;
}//convertNumberToWords

function AmountToWords(numVal)
{
    var junkVal=numVal.value;
    junkVal=Math.floor(junkVal);
    var obStr=new String(junkVal);
    numReversed=obStr.split("");
    actnumber=numReversed.reverse();

    if(Number(junkVal) >=0)
    {
        //do nothing
    }
    else
    {
        //alert('Invalid Number');
        //return false;
        return 'Invalid Number';
    }
    if(Number(junkVal)==0)
    {
       // document.getElementById('container').innerHTML=obStr+''+'Rupees Zero Only';
        return false;
    }
    if(actnumber.length>12)
    {
        //alert('The number conversion is allowed only to the length of 12 characters.');
        //return false;
        return 'The number conversion is allowed only to the length of 12 characters.';
    }

    var iWords=["Zero", " One", " Two", " Three", " Four", " Five", " Six", " Seven", " Eight", " Nine"];
    var ePlace=['Ten', ' Eleven', ' Twelve', ' Thirteen', ' Fourteen', ' Fifteen', ' Sixteen', ' Seventeen', ' Eighteen', ' Nineteen'];
    var tensPlace=['dummy', ' Ten', ' Twenty', ' Thirty', ' Forty', ' Fifty', ' Sixty', ' Seventy', ' Eighty', ' Ninety' ];

    var iWordsLength=numReversed.length;
    var totalWords="";
    var inWords=new Array();
    var finalWord="";
	var currency="";
    j=0;
    for(i=0; i<iWordsLength; i++)
    {

        switch(i)
        {
        case 0:
            if(actnumber[i]==0 || actnumber[i+1]==1 )
            {
                inWords[j]='';
            }
            else
            {
                inWords[j]=iWords[actnumber[i]];
            }
            inWords[j]=inWords[j];
            break;
        case 1:
            tens_complication();
            break;
        case 2:
            hundred_complication();
            break;
        case 3:
			thousand_complication();
            break;
        case 4:
            tens_complication();
            break;
        case 5:
			if(actnumber[i]==0 && actnumber[i+1]==0 && (actnumber[i+2] > 0 || actnumber[i+3] > 0 || actnumber[i+4] > 0|| actnumber[i+5] >= 0))
            {
                inWords[j]= '';
            }
			else if(actnumber[i]==0 || actnumber[i+1]==1 )
			{
				inWords[j]= " Lakh";
			}
            else
            {
                inWords[j]=iWords[actnumber[i]]+" Lakh";
            }

            break;
        case 6:
            tens_complication();
            break;
        case 7:
            if(actnumber[i]==0 || actnumber[i+1]==1 )
            {
                inWords[j]='';
            }
            else
            {
                inWords[j]=iWords[actnumber[i]];
            }
            inWords[j]=inWords[j]+" Crore";
            break;
        case 8:
            tens_complication();
            break;
		case 9:
			hundred_complication();
			break;
		case 10:
			thousand_complication();
			break;
		case 11:
            tens_complication();
            break;
        default:
            break;
        }
        j++;
    }

    function tens_complication()
    {
        if(actnumber[i]==0)
        {
            inWords[j]='';
        }
        else if(actnumber[i]==1)
        {
            inWords[j]=ePlace[actnumber[i-1]];
        }
        else
        {
            inWords[j]=tensPlace[actnumber[i]];
        }
    }

	function hundred_complication()
	{
		 if(actnumber[i]==0)
            {
                inWords[j]='';
            }
            else if(actnumber[i-1]!=0 || (actnumber[i]!=0 && actnumber[i-2]!=0))
            {
                inWords[j]=iWords[actnumber[i]]+' Hundred and ';
            }
            else
            {
                inWords[j]=iWords[actnumber[i]]+' Hundred ';
            }
	}

	function thousand_complication()
	{
		if(actnumber[i]==0 && actnumber[i+1]==0 && (actnumber[i+2] > 0 || actnumber[i+3] > 0 || actnumber[i+4] > 0 || actnumber[i+5] > 0 || actnumber[i+6] > 0 || actnumber[i+7] >= 0))
			{
				inWords[j]='';
			}
			else if(actnumber[i]==0 || actnumber[i+1]==1)
			{
			    inWords[j]=" Thousand ";
			}
			else
			{
				inWords[j]=iWords[actnumber[i]]+" Thousand ";
			}
	}

    inWords.reverse();
    for(i=0; i<inWords.length; i++)
    {
        finalWord+=inWords[i];
    }

	if(finalWord == " One")
		currency = " Rupee";
	else
		currency = " Rupees";
	//alert(finalWord+currency);
        return finalWord+currency;
    //document.getElementById('container').innerHTML=finalWord;
    //return true;
}//AmountToWords

function ValueToWords(numVal)
{
    var junkVal=numVal;
    junkVal=Math.floor(junkVal);
    var obStr=new String(junkVal);
    numReversed=obStr.split("");
    actnumber=numReversed.reverse();

    if(Number(junkVal) >=0)
    {
        //do nothing
    }
    else
    {
        //alert('Invalid Number');
        //return false;
        return 'Invalid Number';
    }
    if(Number(junkVal)==0)
    {
       // document.getElementById('container').innerHTML=obStr+''+'Rupees Zero Only';
        return false;
    }
    if(actnumber.length>12)
    {
        //alert('The number conversion is allowed only to the length of 12 characters.');
        //return false;
        return 'The number conversion is allowed only to the length of 12 characters.';
    }

    var iWords=["Zero", " One", " Two", " Three", " Four", " Five", " Six", " Seven", " Eight", " Nine"];
    var ePlace=['Ten', ' Eleven', ' Twelve', ' Thirteen', ' Fourteen', ' Fifteen', ' Sixteen', ' Seventeen', ' Eighteen', ' Nineteen'];
    var tensPlace=['dummy', ' Ten', ' Twenty', ' Thirty', ' Forty', ' Fifty', ' Sixty', ' Seventy', ' Eighty', ' Ninety' ];

    var iWordsLength=numReversed.length;
    var totalWords="";
    var inWords=new Array();
    var finalWord="";
	var currency="";
    j=0;
    for(i=0; i<iWordsLength; i++)
    {

        switch(i)
        {
        case 0:
            if(actnumber[i]==0 || actnumber[i+1]==1 )
            {
                inWords[j]='';
            }
            else
            {
                inWords[j]=iWords[actnumber[i]];
            }
            inWords[j]=inWords[j];
            break;
        case 1:
            tens_complication();
            break;
        case 2:
            hundred_complication();
            break;
        case 3:
			thousand_complication();
            break;
        case 4:
            tens_complication();
            break;
        case 5:
			if(actnumber[i]==0 && actnumber[i+1]==0 && (actnumber[i+2] > 0 || actnumber[i+3] > 0 || actnumber[i+4] > 0|| actnumber[i+5] >= 0))
            {
                inWords[j]= '';
            }
			else if(actnumber[i]==0 || actnumber[i+1]==1 )
			{
				inWords[j]= " Lakh";
			}
            else
            {
                inWords[j]=iWords[actnumber[i]]+" Lakh";
            }

            break;
        case 6:
            tens_complication();
            break;
        case 7:
            if(actnumber[i]==0 || actnumber[i+1]==1 )
            {
                inWords[j]='';
            }
            else
            {
                inWords[j]=iWords[actnumber[i]];
            }
            inWords[j]=inWords[j]+" Crore";
            break;
        case 8:
            tens_complication();
            break;
		case 9:
			hundred_complication();
			break;
		case 10:
			thousand_complication();
			break;
		case 11:
            tens_complication();
            break;
        default:
            break;
        }
        j++;
    }

    function tens_complication()
    {
        if(actnumber[i]==0)
        {
            inWords[j]='';
        }
        else if(actnumber[i]==1)
        {
            inWords[j]=ePlace[actnumber[i-1]];
        }
        else
        {
            inWords[j]=tensPlace[actnumber[i]];
        }
    }

	function hundred_complication()
	{
		 if(actnumber[i]==0)
            {
                inWords[j]='';
            }
            else if(actnumber[i-1]!=0 || (actnumber[i]!=0 && actnumber[i-2]!=0))
            {
                inWords[j]=iWords[actnumber[i]]+' Hundred and ';
            }
            else
            {
                inWords[j]=iWords[actnumber[i]]+' Hundred ';
            }
	}

	function thousand_complication()
	{
		if(actnumber[i]==0 && actnumber[i+1]==0 && (actnumber[i+2] > 0 || actnumber[i+3] > 0 || actnumber[i+4] > 0 || actnumber[i+5] > 0 || actnumber[i+6] > 0 || actnumber[i+7] >= 0))
			{
				inWords[j]='';
			}
			else if(actnumber[i]==0 || actnumber[i+1]==1)
			{
			    inWords[j]=" Thousand ";
			}
			else
			{
				inWords[j]=iWords[actnumber[i]]+" Thousand ";
			}
	}

    inWords.reverse();
    for(i=0; i<inWords.length; i++)
    {
        finalWord+=inWords[i];
    }
    return finalWord;
}//ValueToWords


function amounttorupepaise(numValObj)
{
	var numval = numValObj.value;
	var ruppe = parseInt(numval);
	var paise = numval.substring(numval.indexOf('.')+1,numval.length);

        var ruppeWord = ValueToWords(ruppe);
	var PaiseWord = ValueToWords(paise);

	if(ruppeWord == " One")
	{
		currency = " Rupee and Paise ";
	}
	else
	{
		currency = " Rupees and Paise ";
	}

	return ruppeWord+currency+PaiseWord;
}//amounttorupepaise

function precise_round(num,decimals)
{
    return Math.round(num*Math.pow(10,decimals))/Math.pow(10,decimals);
}//precise_round

function getCaptchaHash(value) 
{
        var hash = '';
        hash = window.btoa(value);
        return hash;
}//rpCaptchaHash

function getCaptchaVal(value) 
{
    var captcha;
    var alphabets = "AaBbCcDdEeFfGgHhIiJjKkLlMmNnOoPpQqRrSsTtUuVvWwXxYyZz0123456789";
    var first = alphabets[Math.floor(Math.random() * alphabets.length)];
    var second = Math.floor(Math.random() * 10);
    var third = Math.floor(Math.random() * 10);
    var fourth = alphabets[Math.floor(Math.random() * alphabets.length)];
    var fifth = alphabets[Math.floor(Math.random() * alphabets.length)];
    var sixth = alphabets[Math.floor(Math.random() * alphabets.length)];
    captcha = first.toString()+second.toString()+third.toString()+fourth.toString()+fifth.toString()+sixth.toString();
    return captcha;
}//getCaptchaVal

