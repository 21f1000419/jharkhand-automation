0. Citizen login:

a. Open `https://jharnibandhan.gov.in/Citizenentry/citizenlogin` directly.
b. On the Citizen login form, enter the optional saved Citizen credentials in:

```html
<input id="username" name="data[User][username]" type="text" />
<input id="password" name="data[User][password]" type="password" />
```

c. Copy the image from `<img id="captcha_image" src="/users/get_captcha">`, OCR it, and fill:

```html
<input id="captcha" name="data[User][captcha]" type="text" />
```

d. Click `<button id="btnotp" name="btnotp">Get OTP</button>`. When it appears, fill
`<input id="otp" name="data[User][otp]">` manually or through the paired OTP phone, then click
`<button id="btnSubmit" name="btnSubmit">Login</button>`.
e. After Login is clicked, poll until the URL becomes
`https://jharnibandhan.gov.in/Citizenentry/welcome`.
f. After the welcome page opens, open `https://jharnibandhan.gov.in/JHWebService/gras_payment_entry_estamp`
directly and wait for the eStamp form (`#payment_purpose_id`). There is no login timeout; the
flow waits until it succeeds, the user stops it, or the browser is closed.

<!-- Legacy step 1: open Payment Services. Bypassed by direct eStamp navigation. -->
<!-- Legacy step 2: select Purchase eStamp Paper. Bypassed by direct eStamp navigation. -->

CAPTCHA stages: Citizen login, eGRAS login, and eGRAS OTP validation. The eGRAS OTP page
contains a second visible `img.imgcaptcha` / `#txtcaptcha` pair before Validate OTP. After
eGRAS Proceed is clicked, poll for that OTP page instead of requiring a Resume click. After
Validate OTP, poll for both `#rbsbiepay` and `#btnSubmit` before continuing.

3. <form action="" method="post" name="payuForm" autocomplete="off">

        <div class="panel panel-info">
            <div class="panel-heading">
                <div class="panel-title"><h4><b>Estamp Payment (For Non-Registering Deed Types)</b> </h4></div>
                <div><a class="btn btn-info pull-right" target="_blank" href="/helpfiles/Payment/userguide_stamp_payment.pdf">Help</a></div>
            </div>
            <div class="panel-body">
                <input type="hidden" name="RESPONSE_URL" value="http://jharnibandhan.gov.in/JHWebservice/gras_payment_response">

                <input type="hidden" name="requestparam" id="requestparam" class="form-control" value="">

                <div class="row">
                    <div class="col-sm-3">
                        <div class="form-group">
                            <label>Purpose of Payment <span class="star">*</span> </label>
                            <div class="input select"><select name="payment_purpose_id" id="payment_purpose_id" class="form-control input-sm" title="--select--">

<option value="">--select--</option>
<option value="1">To Register Document</option>
<option value="2">For Non-Registering Deed Types</option>
</select></div>                            <span class="form-error" id="payment_purpose_id_error"></span>
                        </div>
                    </div>  
                </div>

                <div class="row">
                    <div class="col-sm-3">
                        <div class="form-group">
                            <label>District <span class="star">*</span> </label>
                            <div class="input select"><select name="district_id" id="district_id" class="form-control input-sm chosen-select select2-hidden-accessible" tabindex="-1" aria-hidden="true">

<option value="">--select--</option>
<option value="25">Bokaro</option>
<option value="11">Chatra</option>
<option value="23">Deoghar</option>
<option value="26">Dhanbad</option>
<option value="29">Dumka</option>
<option value="9">EastSinghbhum</option>
<option value="17">Garhwa</option>
<option value="10">Giridih</option>
<option value="30">Godda</option>
<option value="16">Gumla</option>
<option value="12">Hazaribag</option>
<option value="21">Jamtara</option>
<option value="19">Khunti</option>
<option value="18">Koderma</option>
<option value="13">Latehar</option>
<option value="8">Lohardaga</option>
<option value="20">Pakur</option>
<option value="15">Palamu</option>
<option value="27">Ramgarh</option>
<option value="24">Ranchi</option>
<option value="31">Sahibganj</option>
<option value="22">SaraikelaKharsawan</option>
<option value="14">Simdega</option>
<option value="28">West Singhbhum</option>
</select><span class="select2 select2-container select2-container--default select2-container--below" dir="ltr" style="width: 280px;"><span class="selection"><span class="select2-selection select2-selection--single" role="combobox" aria-haspopup="true" aria-expanded="false" tabindex="0" aria-labelledby="select2-district_id-container"><span class="select2-selection__rendered" id="select2-district_id-container" title="Ranchi">Ranchi</span><span class="select2-selection__arrow" role="presentation"><b role="presentation"></b></span></span></span><span class="dropdown-wrapper" aria-hidden="true"></span></span></div>                            <span class="form-error" id="district_id_error"></span>
                        </div>
                    </div> 
                    <div class="col-sm-3">
                        <div class="form-group">
                            <label>Article <span class="star">*</span> </label>
                            <div class="input select"><select name="article_id" id="article_id" class="form-control input-sm chosen-select select2-hidden-accessible" tabindex="-1" aria-hidden="true">
<option value="">--select--</option>
<option value="1">Acknowledgement</option>
<option value="13">Administration Bond</option>
<option value="87">Adoption Deed</option>
<option value="3">Affidavit</option>
<option value="4">Agreement or Memorandum of an Agreement</option>
<option value="6">Agreement Relating to Deposit of Title Deeds,Pawn</option>
<option value="75">Appointment In Execution of a power</option>
<option value="107">Appraisement of valuation</option>
<option value="9">Apprenticeship Deed</option>
<option value="10">Articles of Association of a company</option>
<option value="11">Articles of Clerkship</option>
<option value="12">Award</option>
<option value="119">Bond</option>
<option value="161">Cancellation</option>
<option value="92">Certificate of Sale</option>
<option value="18">Charter Party</option>
<option value="24">Composition Deed</option>
<option value="91">Conveyance</option>
<option value="26">Copy or Extract</option>
<option value="27">Counter part or Duplicate</option>
<option value="28">Custom Bonds</option>
<option value="117">Debenture</option>
<option value="32">Deed of Exchange</option>
<option value="30">Divorce</option>
<option value="111">Entry as an advocate, on the roll of any High Court.</option>
<option value="34">Gift</option>
<option value="77">Indemnity Bond</option>
<option value="33">Instrument of Further Charge</option>
<option value="89">Lease</option>
<option value="38">Letter of Licence</option>
<option value="39">Memorandom of Association of a company</option>
<option value="40">Mortgage Deed</option>
<option value="41">Mortgage of a crop</option>
<option value="42">Notarial Act</option>
<option value="43">Note of Memorandum</option>
<option value="44">Note of Protest by Master of a Ship</option>
<option value="46">Partition</option>
<option value="47">Partnership</option>
<option value="48">Power of Attorney</option>
<option value="50">Protest of a Master of a Ship</option>
<option value="49">Protest of Bill or Note</option>
<option value="51">Reconveyance of Mortgaged Property</option>
<option value="52">Release Deed</option>
<option value="53">Respodentia Bond</option>
<option value="54">Security Bond</option>
<option value="55">Settlement Deed</option>
<option value="56">Share Warrants</option>
<option value="58">Surrender of Lease</option>
<option value="59">Transfer</option>
<option value="60">Transfer of Lease</option>
<option value="61">Trust</option>
</select><span class="select2 select2-container select2-container--default select2-container--below" dir="ltr" style="width: 280px;"><span class="selection"><span class="select2-selection select2-selection--single" role="combobox" aria-haspopup="true" aria-expanded="false" tabindex="0" aria-labelledby="select2-article_id-container"><span class="select2-selection__rendered" id="select2-article_id-container" title="Agreement or Memorandum of an Agreement">Agreement or Memorandum of an Agreement</span><span class="select2-selection__arrow" role="presentation"><b role="presentation"></b></span></span></span><span class="dropdown-wrapper" aria-hidden="true"></span></span></div>                            <span class="form-error" id="article_id_error"></span>
                        </div>
                    </div>
                </div>
                <div class="row">
                    <div class="col-sm-6">
                        <div class="form-group">
                            <label>First Party Name <span class="star">*</span></label>
                            <div>
                                <input type="text" name="party1_fullname_en" id="party1_fullname_en" class="form-control" value="" placeholder="Enter First Party Name" title="">
                            </div>
                            <span class="form-error" id="party1_fullname_en_error"></span>
                        </div>
                    </div>
                </div>

                <div class="row">
                    <div class="col-sm-6">
                        <div class="form-group">
                            <label>Second Party Name <span class="star">*</span></label>
                            <div class="">
                                <input type="text" name="party2_fullname_en" id="party2_fullname_en" class="form-control" value="" placeholder="Enter NIL if not applicable" title="">
                            </div>
                            <span class="form-error" id="party2_fullname_en_error"></span>
                        </div>
                    </div>
                </div>
                <div class="row">
                    <div class="col-sm-6">
                        <div class="form-group">
                            <label>Stamp Duty Paid By <span class="star">*</span> </label>
                            <div class="">
                                <input name="payee_fname_en" id="payee_fname_en" class="form-control" value="" placeholder="Enter Payee Name">
                            </div>
                            <span class="form-error" id="payee_fname_en_error"></span>
                        </div>
                    </div>
                </div>

                <div class="row">
                    <div class="col-sm-6">
                        <div class="form-group">
                            <label>Purpose of Stamp  <span class="star">*</span> </label>
                            <div class="">
                                <input name="payment_reason" id="payment_reason" class="form-control" value="" placeholder="Enter Purpose of Stamp Duty Paid">
                            </div>
                            <span class="form-error" id="payment_reason_error"></span>
                        </div>
                    </div>
                </div>
                <div class="row">
                    <div class="col-sm-3">
                        <div class="form-group">
                            <label>PAN NO <span class="star"></span> </label>
                            <div>
                                <input name="PANNO" id="PANNO" class="form-control" value="" placeholder="Enter Pan Number">
                            </div>
                            <span class="form-error" id="PANNO_error"></span>
                        </div>
                    </div>
                    <div class="col-sm-3">
                        <div class="form-group">
                            <label>Mobile Number <span class="star">*</span> </label>
                            <div>
                                <input name="mobile" id="mobile" class="form-control" value="9461743576">
                            </div>
                            <span class="form-error" id="mobile_error"></span>
                        </div>
                    </div>
                </div>
                <div class="row">
                    <div class="col-sm-3">
                        <div class="form-group">
                            <label>Total Amount <span class="star">*</span></label>
                            <div class="">
                                <input type="text" name="AMOUNT" id="AMOUNT" class="form-control" value="0" title="0">
                            </div>
                            <span class="form-error" id="AMOUNT_error"></span>
                        </div>
                    </div>
                </div>
            </div>
            <div class="panel-footer">
                <div class="panel-title">

                        <p class="star"> Note : Please check again whether you are paying stamp duty for registration purposes or for non-registration purposes.</p>
                        <button type="button" class="btn btn-primary" data-toggle="modal" data-target="#exampleModal" id="launchmodal">
                            Proceed to Pay
                        </button>
                                    </div>
            </div>
        </div>

        <div class="modal fade" id="exampleModal" tabindex="-1" role="dialog" aria-labelledby="exampleModalLabel" aria-hidden="true">
            <div class="modal-dialog" role="document">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="exampleModalLabel">Confirmation</h5>
                        <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                            <span aria-hidden="true">×</span>
                        </button>
                    </div>
                    <div class="modal-body">
                        <table class="table">
                            <tbody id="confirmdiv">

                            </tbody>


                        </table>
                    </div>
                    <div class="modal-footer">
                                                    <input type="submit" class="btn btn-primary pull-left" value="Pay Now">
                                                <button type="button" class="btn btn-secondary pull-right" data-dismiss="modal">Close</button>

                    </div>
                </div>
            </div>
        </div>


    </form>

4. <div class="modal-content">
<div class="modal-header">
<h5 class="modal-title" id="exampleModalLabel">Confirmation</h5>
<button type="button" class="close" data-dismiss="modal" aria-label="Close">
<span aria-hidden="true">×</span>
</button>
</div>
<div class="modal-body">
<table class="table">
<tbody id="confirmdiv"><tr><td>District Name</td><td>Ranchi</td></tr><tr><td>Article Name</td><td>Agreement or Memorandum of an Agreement</td></tr><tr><td>Party 1 Name</td><td>AS BULLISHIFY PRIVATE LIMITED</td></tr><tr><td>Party 2 Name</td><td>NA</td></tr><tr><td>Stamp Duty Paid By</td><td>AS BULLISHIFY PRIVATE LIMITED</td></tr><tr><td>Purpose of Stamp</td><td>INTEGRAL TO THE ENCLOSED DDPI EXECUTED BY THE CLIENT FOR THE COMPANY</td></tr><tr><td>Pan Number</td><td></td></tr><tr><td>Mobile Number</td><td>9461743576</td></tr><tr><td>Amount</td><td>50</td></tr></tbody>

                        </table>
                    </div>
                    <div class="modal-footer">
                                                    <input type="submit" class="btn btn-primary pull-left" value="Pay Now">
                                                <button type="button" class="btn btn-secondary pull-right" data-dismiss="modal">Close</button>

                    </div>
                </div>

5. <div class="modal-footer">
<div class="modal-body pad-mod">
<input type="checkbox" name="ch" class="one" value="1" id="takenBefore">
<span class="text-danger" style="font-size: 11px;"> I agree with terms and conditions/मैं उपरोक्त नियमों और शर्तों को स्वीकार करने के लिए सहमत हूँ |</span>
</div>
<button type="button" class="close_model btn btn-danger">OK</button>
</div>

6. <div role="tabpanel" class="tab-pane active" id="home">
                                        <h4 class="text-center mt-2 mb-2" style="text-transform: uppercase;background: #17a2b8ad;color: #ffff;font-weight: bold;">
                                            Login</h4>
                                            <label id="lblcommonMsg" style="color: Red"></label>
                                        <div class="form-row">
                                            <div class="form-group col-md-12">
                                                <label for="inputName">
                                                    Your Username</label>
                                                <input name="txtLoginId" type="text" maxlength="100" id="txtLoginId" class="form-control" placeholder="Enter your username">
                                                <span id="rfvtxtLoginId" style="color:Red;display:none;">Enter User Name</span>
                                            </div>
                                            <div class="form-group col-md-12">
                                                <label for="inputName">
                                                    Your Password</label>
                                                <input name="txtPassword" type="password" maxlength="50" id="txtPassword" class="form-control" placeholder="Enter your Password">
                                                <img class="imgcaptcha" src="data:image/gif;base64,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" style="height:50px;width:150px;">
                                                <input type="image" name="ImageButton1" id="ImageButton1" class="rigcp" src="images/refresh.png">
                                            </div>
                                            <div class="form-group col-md-12">
                                                <label for="dlno">
                                                    Enter Captcha</label>
                                                <input name="txtcaptcha" type="text" maxlength="6" id="txtcaptcha" class="form-control" autocomplete="off" placeholder="" style="text-transform: uppercase;">
                                            </div>
                                        </div>

                                    </div>



                                    <input type="submit" name="btnproceed" value="Proceed" onclick="return IsvalidateForm();" id="btnproceed" class="btn btn-success ">

7. <div class="tab-content">
                                    <div role="tabpanel" class="tab-pane active" id="home">
                                        <h4 class="text-center mt-2 mb-2 bg-info text-white">
                                            Two Factor Authentication</h4>
                                            <div>
                                               <span id="lblMsg" style="color:Red;"></span>
                                            </div>

                                        <label id="lblcommonMsg" style="color: Red">
                                        </label>
                                        <div id="divOtpMsg" style="color:#2f8a19dd;font-weight: bold;">Your OTP has been sent to registered mobile no. 94XXXX3576 with OTP Reference number :  2163352</div>
                                        <div class="form-row">
                                          <div class="col-md-6">
                                             <label for="inputName">
                                                    Enter OTP</label>
                                                <input name="txtOTP" type="text" maxlength="6" id="txtOTP" class="form-control" placeholder="Enter OTP">
                                                <span id="RequiredFieldValidator1" style="color:Red;display:none;">Enter OTP</span>
                                          </div>
                                           <div class="col-md-6">
                                           <br>
                                              <span id="timer" style="color:Red;font-size: 12px">Time Remaining :  290 seconds</span>
                                          </div>
                                          </div>
                                           <div class="form-row">
                                            <div class="form-group col-md-6">
                                                <label for="dlno">
                                                    Enter Captcha</label>
                                                <input name="txtcaptcha" type="text" maxlength="6" id="txtcaptcha" class="form-control" autocomplete="off" placeholder="Enter Captcha" style="text-transform:uppercase">
                                            </div>
                                            <div class="form-group col-md-6">
                                       <br>
                                                <img class="imgcaptcha" src="data:image/gif;base64,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" style="height:50px;width:150px;">
                                                <input type="image" name="ImageButton1" id="ImageButton1" class="rigcp" src="images/refresh.png">
                                            </div>
                                        </div>


                                            <div class="form-row">
                                             <div class="col-md-4">
                                              <input type="submit" name="btnResendOTP" value="Re Send OTP" id="btnResendOTP" class="btn btn-link1" form-type="inquiry" disabled="">
                                              </div>
                                               <div class="col-md-4">

                                              </div>
                                               <div class="col-md-4">
                                                 <input type="submit" name="btnproceed" value="Validate OTP" onclick="return IsvalidateForm();" id="btnproceed" class="btn btn-success full-width left">
                                              </div>

                                            </div>
                                    </div>
                                </div>

8. <div id="divSBI" class="row bod-top no-pad">

                    <span class="rdb-pad" style="font-size:15px;font-weight:bold;"><input id="rbsbiepay" type="radio" name="a" value="rbsbiepay" checked="checked"></span>

                     <div class="col-md-6">
                      <img src="css/images/sbiepay.png" class="" style="">

                     <span id="lblsuceesratesbi" class="badge badge-pill badge-success p-1" style="font-size: 12px;font-family:

   : Courier New;">Success Rate : 94 %</span>
   </div>
   </div>

                     <input type="submit" name="btnSubmit" value="Pay Rs 50" id="btnSubmit" class="btn btn-success m-3">

9. <table id="ContentPlaceHolder1_rblagree" class="radio">
    		<tbody><tr>
    			<td><input id="ContentPlaceHolder1_rblagree_0" type="radio" name="ctl00$ContentPlaceHolder1$rblagree" value="Y"><label for="ContentPlaceHolder1_rblagree_0">Agree</label></td><td><input id="ContentPlaceHolder1_rblagree_1" type="radio" name="ctl00$ContentPlaceHolder1$rblagree" value="N"><label for="ContentPlaceHolder1_rblagree_1">Not Agree</label></td>
    		</tr>
    	</tbody></table>

        <a onclick="javascript:return confirm('Are you sure want to confirm');" id="ContentPlaceHolder1_btncontinue" class="btn btn-success" href="javascript:__doPostBack('ctl00$ContentPlaceHolder1$btncontinue','')" style="font-weight:bold;">Proceed For Payment</a>

10. Current payment mode: when the SBI hosted URL
`https://epay.sbi.bank.in/secure/AggregatorHostedListener#no-back-button` opens, select:

```html
<li id="activeUPI" class="activeUPI"><a href="#" class="collapseup">UPI</a></li>
```

Then select `<input id="upiQR1" type="radio">` (UPI QR), click `<button id="upiButton">Pay Now</button>`,
and poll for the final eStamp download link while the user completes the QR payment on their phone.

The following card-form HTML is retained only as a legacy portal snapshot; payment automation uses UPI.

<div class="row">
    		<div class="col-md-12 txt">
    			<!--<h4 class="txt">Please enter your card details</h4>-->
    			<h4 class="txt" style="color: #FF0000;font-size: 14px;">Please ensure that your card is enabled for online (E-Commerce) transactions</h4>
    			<h4 class="txt" style="color: #FF0000;font-size: 14px;">
    				कृपया&nbsp;
    				सुनिच्छित&nbsp;
    				  करे&nbsp;
    				  कि&nbsp;  
    				  आपका&nbsp;
    				  कार्ड&nbsp;
    				(ई-कॉमर्स)&nbsp;
    				लेनदेन&nbsp;
    				के&nbsp;
    				लिए&nbsp;
    				सक्षम&nbsp;
    				है
    			</h4>
    		</div>
    		<div id="carddownbelow"></div>
    		<div class="col-md-12 padTop10">
    			<div class="form-group">

        		<input type="hidden" id="pgPaygtwid" value="94">
        			<label for="">Card Number</label>
        			<div id="cardNum" class="input-group">
        				<span class="staticParent">
        					<input type="text" autocomplete="off" class="form-control validate_number bgPayProcImg" name="fieldoneCredit" id="card_number" maxlength="23" onpaste="return false" oncopy="return false" style="background-image: url(&quot;&quot;);">
        				</span>
        				<span id="spanVisa" class="input-group-addon noborder">
        					<img src="images/visa_logo.png" alt="Visa" class="img-responsive card-img">
        				</span>
        				<span id="spanmMstercard" class="input-group-addon noborder">
        					<img src="images/mastercard_logo.png" alt="Visa" class="img-responsive card-img">
        				</span>
        				<!-- <span id="spanAmericanxprss" class="input-group-addon noborder">
        					<img src="images/americanxprss_logo.png" alt="Visa" class="img-responsive card-img">
        				</span>  -->  <!--commented amex logo - for now not in use-->
        				<span id="spanRupay" class="input-group-addon">
        					<img src="images/rupay_logo.png" alt="Visa" class="img-responsive card-img">
        				</span>
        			</div>
        		<div class="alert alert-danger" id="CardNoErrorMsg" style="display: none;"></div>
        		<div class="alert alert-danger" id="ecomErrorMsg" style="display: none;"></div>
        	</div>
        	</div>
        	<div id="paymodePayProc" style="display: none;">
        		<div class="col-md-6 ">
        			<div class="form-group">
        				<label for="">Card Type</label>
        				<select class="form-control  show-tick" autocomplete="off" name="cardPaymode" id="cardPaymode" data-live-search="true" title="Select Card Type" required="">
        					<option value="" hidden="">Card Type</option>

        					<option value="CC">Credit Card</option>

        					<option value="DC">Debit Card</option>

        				</select>
        			</div>
        		</div>
        		<div id="cardSchema" class="col-md-6">
        			<div class="form-group">
        				<label for="">Card Scheme</label>
        				<select class="form-control show-tick" autocomplete="off" id="cardPayproc" name="cardPayproc" data-live-search="true" title="Select Card Scheme" required="">
        				<option value="" disabled="" selected="">Card Scheme</option>
        				</select>
        			</div>
        		</div>

        	</div>
        	<div class="col-md-12"><div class="alert alert-danger" id="payModeError" style="display: none;"></div></div>

    <div class="col-md-12">
    			<div class="form-group" id="nameOnCardDiv">
    				<label for="">Name of the card holder</label> 
    				<input type="text" autocomplete="off" name="custCardName" id="custCardName" disabled="" class="form-control disableattr" minlength="2" maxlength="45" placeholder="Name as on card" required="" onpaste="return false" oncopy="return false">
    				<div class="alert alert-danger" id="cardNameErrorMsg" style="display: none;"></div>
    			</div>
    		</div>
    		<div id="divYear">
    			<div class="col-md-4">
    				<div class="form-group">
    					<label for="">Expiry Date/Valid Thru</label> 
    					<select class="form-control show-tick disableattr" autocomplete="off" id="expMonth" name="expMonthCard" data-live-search="true" disabled="" title="Select Month" required="">
    						<option value="">Month</option>
    						<option value="01">January (01)</option>
    						<option value="02">February (02)</option>
    						<option value="03">March (03)</option>
    						<option value="04">April (04)</option>
    						<option value="05">May (05)</option>
    						<option value="06">June (06)</option>
    						<option value="07">July (07)</option>
    						<option value="08">August (08)</option>
    						<option value="09">September (09)</option>
    						<option value="10">October (10)</option>
    						<option value="11">November (11)</option>
    						<option value="12">December (12)</option>
    					</select>
    				</div>
    			</div>
    			<div class="col-md-4">
    				<div class="form-group">
    					<label for=""> &nbsp;</label> 
    					<select class="form-control show-tick disableattr" autocomplete="off" name="expYearCard" id="expYear" disabled="" data-live-search="true" title="Select Year" required="">
    						<option value="">Year</option>
    					<option value="2026">2026</option><option value="2027">2027</option><option value="2028">2028</option><option value="2029">2029</option><option value="2030">2030</option><option value="2031">2031</option><option value="2032">2032</option><option value="2033">2033</option><option value="2034">2034</option><option value="2035">2035</option><option value="2036">2036</option><option value="2037">2037</option><option value="2038">2038</option><option value="2039">2039</option><option value="2040">2040</option><option value="2041">2041</option><option value="2042">2042</option><option value="2043">2043</option><option value="2044">2044</option><option value="2045">2045</option></select>
    				</div>
    			</div>
    		</div>
    		<div class="col-md-4">
    			<div class="form-group" id="cvvNoInfo">
    				<label for="" id="cvvLabelDiv">CVV/CVC</label>
    				<label for="" id="dbcLabelDiv">4-DBC</label>
    				<div class="input-group">
    					<span class="staticParent">
    					<input type="password" autocomplete="off" class="form-control disableattr validate_number" name="cvvCard" id="cvv" maxlength="3" disabled="" onpaste="return false" oncopy="return false" required="">
    					</span> 
    					<span class="input-group-addon info-addon"> 
    					<span class="glyphicon glyphicon-info-sign" id="cvvImageDiv" data-toggle="tooltip" title="" data-placement="bottom" data-original-title="CVV number is the 3-digit number found on the back of your card near the signature panel."></span>
    					<span class="glyphicon glyphicon-info-sign" id="dbcImageDiv" data-toggle="tooltip" title="" data-placement="bottom" style="display:none" data-original-title="4-DBC is the 4-digit number present on the front side of the card right above the card number"></span>
    					</span>
    				</div>

        		</div>
        	</div>
        	<div class="col-md-12">
        		<div class="alert alert-danger" id="expMonthYearErrorMsg" style="display: none;"></div>
        		<div class="alert alert-danger" id="cvvCardErrorMsg" style="display: none;"></div>
        	</div>
        	<div class="col-md-12">
        		<div class="form-group" id="bankNameDisp" style="display: none;">
        			<label for="">Bank Name</label>
        			<input type="text" autocomplete="off" name="custCardBankName" id="custCardBank" disabled="" class="form-control disableattr" maxlength="100" placeholder="Bank Name" required="">
        		</div>
        	</div>
        <!-- </div> -->

    <!--********  GSTN Changes start **********  -->

        		<div>
        		<div class="panel-body">

<meta http-equiv="Content-Type" content="text/html; charset=ISO-8859-1">
<title>Insert title here</title>
<style>
	.gstFormCheck{
		margin: 10px 0;
	}
	.gstFormCheck .form-check-input{
		    margin-top: 12px;
	}
	.gstFormCheck .form-check-label{
		font-weight: 400;
	}
</style>

    	<div class="col-md-12" style="padding: 0;">
    		<button class="btn usegstin gstin_txt" name="gstCCDC" id="gstCCDC" value="CCDC" style="display: none;">
    			<img src="images/tick.png">&nbsp;&nbsp;&nbsp;Use your GSTIN for
    			claiming input tax&nbsp;<label style="color: #FF0000">(Optional)<!--<label-->
    			</label></button>
    	<div class="gstin-content" style="display: none">
    		<button class="btn gstin_txt">
    			Use your GSTIN for claiming input tax&nbsp;<label style="color: #FF0000">(Optional)<!--<label-->&nbsp;&nbsp;&nbsp;<img src="images/cross-icon.jpg" class="btn cross_icon" name="crossclkgstinCCDC" id="crossclkgstinCCDC" value="CCDC">
    		</label></button>
    		<div class="clearfix"></div>
    		<div class="row" style="margin: 0 auto;">
    			<div class="col-md-12">
    				<div class="form-group">
    					<input type="text" name="gstnNoCCDC" id="gstnNoCCDC" class="form-control onKeyUpValidateGST" placeholder="GSTIN" autocomplete="off" onkeyup="validateGSTNKeyUp('gstnNo','CCDC');" maxlength="15" oncopy="return false" onpaste="return false" oncut="return false" onblur="blurFunction('gstnNo','CCDC')">
    				</div>
    			</div>

    			<div class="col-md-4">
    				<div class="form-group">
    					<input type="text" name="legalNameCCDC" id="legalNameCCDC" class="clearablefld form-control" placeholder="Legal Name" maxlength="50" autocomplete="off" onkeyup="validateGSTNKeyUp('legalNameCCDC','CCDC');" oncopy="return false" onpaste="return false" oncut="return false">

    				</div>

    			</div>

    			<div class="col-md-4">
    				<div class="form-group">
    					<input type="text" name="pinCodeCCDC" id="pinCodeCCDC" class="clearablefld form-control" placeholder="PIN" maxlength="6" autocomplete="off" onkeyup="validateGSTNKeyUp('pinCode','CCDC')" oncopy="return false" onpaste="return false" oncut="return false">
    				</div>
    			</div>

    			<div class="col-md-4">
    				<div class="form-group">
    					<input type="text" name="stateCCDC" id="stateCCDC" class="clearablefld form-control" placeholder="State" autocomplete="off" onkeyup="validateGSTNKeyUp('stateCCDC','CCDC');">
    				</div>
    			</div>
    			<div class="col-md-4">
    				<div class="form-group">
    					<input type="text" name="emailIdCCDC" id="emailIdCCDC" class="clearablefld form-control onKeyUpValidateEmail" placeholder="Email ID" maxlength="30" autocomplete="off" oncopy="return false" onpaste="return false" oncut="return false">
    				</div>
    			</div>
    			<div class="col-md-4">
    				<div class="form-group">
    					<input type="text" name="mobNoCCDC" id="mobNoCCDC" class="clearablefld form-control" placeholder="Mobile no." maxlength="10" autocomplete="off" onkeyup="validateGSTNKeyUp('mobNoCCDC','CCDC');" oncopy="return false" onpaste="return false" oncut="return false">
    				</div>
    			</div>

    			<div class="col-md-4">
    				<div class="form-group">
    					<input type="text" name="firstNameCCDC" id="firstNameCCDC" class="clearablefld form-control" placeholder="First Name" maxlength="50" autocomplete="off" onkeyup="validateGSTNKeyUp('firstNameCCDC','CCDC');" oncopy="return false" onpaste="return false" oncut="return false">

    				</div>

    			</div>

    			<div class="col-md-4">
    				<div class="form-group">
    					<input type="text" name="lastNameCCDC" id="lastNameCCDC" class="clearablefld form-control" placeholder="Last Name" maxlength="50" autocomplete="off" onkeyup="validateGSTNKeyUp('lastNameCCDC','CCDC');" oncopy="return false" onpaste="return false" oncut="return false">
    				</div>
    			</div>

    			<div class="col-md-4">
    				<div class="form-group">
    					<button type="button" id="ccdcGSTNBtn" class="btn btn_info btn_update btn_up" onclick="validateGSTNParameter('CCDC');">Update</button>
    				</div>

    			</div>

    		</div>
    		<div class="row" style="margin: 0 auto;">
    			<div class="form-check gstFormCheck">
    			<input class="chkgstbox form-check-input" type="checkbox" id="GSTNCheckedCCDC" name="GSTNCheckedCCDC" value="CCDC">
    			<label class="chkgstbox form-check-label consentLabel" for="GSTNCheckedCCDC">
    				I confirm that the GSTIN details entered is valid and belongs to me
    				or the organization I represent. I authorize SBIePay to use this
    				GSTIN details to generate tax invoices and report to GST authorities
    				in accordance with applicable tax laws.
    			</label>
    		</div>
    		<div class="alert alert-danger" id="gstnErrorMsgCCDC" style="display: none;"></div>
    		</div>

    	</div>
    	<div class="gstin-content2" style="display: none">
    		<p class="gstin_txt">Use your GSTIN for claiming input tax&nbsp;<label style="color: #FF0000">(Optional)<!--<label-->&nbsp;&nbsp;&nbsp;</label></p>
                   <div id="collapse2">
                     <p class="expand collapsed" data-toggle="collapse" data-target="#ccdcCollpase" aria-expanded="false"></p>
                   </div>
    		<table class="table gstintbl" style="margin-bottom: 0">
    			<tbody><tr>
    				<th style="width: 150px;">GSTIN</th>
    				<td id="gstinNoCCDC"></td>
    			</tr>
    			<tr>
    				<th style="width: 150px;">Legal Name</th>
    				<td id="gstnLegalNameCCDC"></td>
    			</tr>
    		</tbody></table>

    		<table class="table gstintbl collapse" id="ccdcCollpase">
    			<tbody><tr>
    				<th style="width: 150px;">First Name</th>
    				<td id="gstnFNameCCDC"></td>
    			</tr>
    			<tr>
    				<th style="width: 150px;">Address</th>
    				<td id="gstnAddrCCDC"></td>
    			</tr>
    			<tr>
    				<th style="width: 150px;">Email ID</th>
    				<td id="gstnEmailCCDC"></td>
    			</tr>
    			<tr>
    				<th style="width: 150px;">Mobile No</th>
    				<td id="gstnMobCCDC"></td>
    			</tr>
    		</tbody></table>
    		<div class="clearfix"></div>
    		<div class="row">
    			<div class="col-md-4">
    				<div class="form-group">
    					<button type="button" class="btn_info btn_modify btn_mdfy">Modify</button>
    				</div>
    			</div>
    			<div class="col-md-4">
    				<div class="form-group">
    					<button type="button" class="btn_modify cross_icon btn_remove" name="gstRmClickCCDC" id="gstRmClickCCDC" value="CCDC" onclick="setGSTNBeanAjax('remove')">Remove</button>
    				</div>
    			</div>
    		</div>
    	</div>
    	</div>



    		</div>
    		</div>
    	<!--********  GSTN Changes end**********  -->
    		<div class="col-xs-12">
    			<button type="button" name="credit" id="cardSubButton" onclick="return payNowSubmit('cardPayment',this)" class="btn pay_btn btn01">Pay Now</button>
    			<input type="hidden" name="cardPaymentPayproc" id="cardPaymentPayproc">
    		</div>
    		<div class="col-xs-12 text-right">
    			<a onclick="submitCancelRequest(this);" href="#"> Cancel</a>
    		</div>

    </div>

11. pyament otp or confirmation step might be missing in case if otp is required with card

<div class="otpFlexMain">
							<div class="otpFlex">
								<input type="password" class="form-control" id="otpId" name="otpId">
								<button type="button" id="verifyOtp" value="Verify OTP" class="btn verifyOtp">Verify OTP</button>

    						</div>
    						<div class="resendOtpDiv">
    							<button type="button" id="resendOtp" class="btn resendOtp" value="Resend OTP">Resend OTP</button>
    						</div>
    					</div>

12. a. <div class="panel-footer center">
<a href="/JHWebService/gras_estamp_download/7489afcddfd00e8d892a" class="btn btn-success" target="_blank">eStamp</a>
<button class="btn btn-info" id="printbtn">Print</button>
</div>

12. b. handle accordingly: "Transaction Failed! NA"
