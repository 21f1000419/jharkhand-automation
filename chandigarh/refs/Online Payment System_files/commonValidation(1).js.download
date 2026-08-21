/*
* File Name    : commonValidation.js
 * @author      : Rahul Siwach
 * @version     : 1.0
 * Change Log   :
 *
 *   Date         Version  Modifier			Description.
 * ----------     -------  ---------		---------------------
*/
function validemailTemp(fieldName)
{
            var fieldValue  = fieldName.value;
            var fieldLength = fieldValue.length;

            if (fieldValue.trim !="" && fieldLength < 8 )
            {
            	alert("Please Enter a valid Email Address");
                fieldName.focus();
                fieldName.select();
            } else {
                if( /^\w+([\.-]?\w+)*@\w+([\.-]?\w+)*(\.\w{2,3})+$/.test( fieldValue ))
                {
                	return true;
                } else {
                        alert("Please Enter a valid Email Address");
                        fieldName.focus();
                        fieldName.select();
                }
            }
            return false;
}
