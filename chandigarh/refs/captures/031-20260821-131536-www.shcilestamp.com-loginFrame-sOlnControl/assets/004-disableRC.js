/*
* File Name    : disableRC.js
 * @author      : Rahul Siwach
 * @version     : 1.0
 * Change Log   :
 *
 *   Date         Version  Modifier			Description.
 * ----------     -------  ---------		---------------------
*/
function byteArrayToStringTemp(a)
{
	for(var b="",d=0;d<a.length;d++)
	if(a[d]!=0)b+=String.fromCharCode(a[d]);
	return b
}