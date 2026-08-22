/* 
 * To change this license header, choose License Headers in Project Properties.
 * To change this template file, choose Tools | Templates
 * and open the template in the editor.
 */
var errmsg = "";

function BrowserVersionchck(msg) {

    navigator.browserSpecs = (function () {
        var ua = navigator.userAgent, tem,
                M = ua.match(/(opera|chrome|safari|firefox|msie|trident(?=\/))\/?\s*(\d+)/i) || [];
        if (/trident/i.test(M[1])) {
            tem = /\brv[ :]+(\d+)/g.exec(ua) || [];
            return {name: 'IE', version: (tem[1] || '')};
        }
        if (M[1] === 'Chrome') {
            tem = ua.match(/\b(OPR|Edge)\/(\d+)/);
            if (tem != null)
                return {name: tem[1].replace('OPR', 'Opera'), version: tem[2]};
        }
        M = M[2] ? [M[1], M[2]] : [navigator.appName, navigator.appVersion, '-?'];
        if ((tem = ua.match(/version\/(\d+)/i)) != null)
            M.splice(1, 1, tem[1]);
        return {name: M[0], version: M[1]};
    })();

//    console.log(navigator.browserSpecs); //Object { name: "Firefox", version: "42" }


//errmsg ="name : "+navigator.browserSpecs.name + "   version  : " +navigator.browserSpecs.version ;

    if (navigator.browserSpecs.name == 'Firefox') {
        // Do something for Firefox.
        if (navigator.browserSpecs.version < 35) {
//            errmsg = " Compatible Versions : Mozilla Firefox versions 35 and above. ";
            errmsg = "Browser version is not compatibale with this application.You may not be able to access some functionality so please upgrade to latest version. ";
        }
    } else if (navigator.browserSpecs.name == 'IE' || navigator.browserSpecs.name == 'MSIE') {
        // Do something for all other browsers.
        if (navigator.browserSpecs.version < 10) {
//            errmsg =  "Compatible Versions : Internet explorer versions 10 and above. ";
            errmsg = "Browser version is not compatibale with this application.You may not be able to access some functionality so please upgrade to latest version.";
        }
    } else if (navigator.browserSpecs.name == 'Chrome') {
        // Do something for all other browsers.
        if (navigator.browserSpecs.version < 50) {
//            errmsg = " Compatible Versions : Google Chrome versions 50 and above. ";
            errmsg = "Browser version is not compatibale with this application.You may not be able to access some functionality so please upgrade to latest version.";
        }
    }
    return errmsg;

}